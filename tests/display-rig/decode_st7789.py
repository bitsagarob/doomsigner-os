#!/usr/bin/env python3
"""Turn captured SPI traffic back into the picture the panel would have shown.

The device's own ST7789 driver runs unmodified and writes to a real
/dev/spidev0.0. ss_display_capture.ko records every message together with the
level of the data/command line, which is the only thing that distinguishes an
ST7789 command from pixel data. This replays that stream through the panel's own
state machine and writes a PNG.

Because it decodes the real bytes, it fails the way the panel would: if the
driver sends the colour bytes in the wrong order, or addresses the wrong window,
the PNG is wrong in exactly the way the screen would have been.

    python3 decode_st7789.py capture.bin out.png
"""

import struct
import sys
import zlib

HEADER = struct.Struct("<BBHI")
FLAG_GPIO = 0x0001

CMD_SWRESET = 0x01
CMD_INVOFF = 0x20
CMD_INVON = 0x21
CMD_CASET = 0x2A
CMD_RASET = 0x2B
CMD_RAMWR = 0x2C
CMD_MADCTL = 0x36
CMD_COLMOD = 0x3A

LINE_DC, LINE_RST, LINE_BL = 25, 27, 24


class Panel:
    """Enough of an ST7789 to reconstruct what it was told to display."""

    def __init__(self, width=240, height=240):
        self.width = width
        self.height = height
        self.pixels = bytearray(width * height * 3)
        self.inverted = False
        self.madctl = 0x00
        self.colmod = 0x05  # 16 bits per pixel
        self.x0 = self.y0 = 0
        self.x1 = width - 1
        self.y1 = height - 1
        self.cursor = None
        self.frames = []
        self.frame_bytes = 0
        self.pending = bytearray()
        self.args = []
        self.cmd = None
        self.writes = 0

    # ---- command stream ----

    def command(self, code):
        self.finish_args()
        self.cmd = code
        self.args = []
        if code == CMD_RAMWR:
            self.clamp_window()
            self.cursor = (self.x0, self.y0)
            self.pending = bytearray()
            self.frame_bytes = 0
        elif code == CMD_INVON:
            # Recorded, deliberately not applied. INVON is part of the normal
            # init for this IPS panel: its glass is wired inverted, so turning
            # inversion on is what makes the colours come out right. Applying a
            # 255-x here would misreport every single frame as its own negative.
            # A frame that arrives with inversion OFF is the interesting case.
            self.inverted = True
        elif code == CMD_INVOFF:
            self.inverted = False
        elif code == CMD_SWRESET:
            self.__init__(self.width, self.height)

    def clamp_window(self):
        """
        An end address past the controller's RAM is ignored (ST7789V datasheet,
        CASET and RASET notes), so the last real column or row ends the window.
        With MADCTL's row/column exchange the RAM is 320 wide and 240 tall.
        SeedSigner's st7789_mpy.py ends its 320x240 window at 320 and 240, past
        both, and draws upright on real panels. The same off-by-one at 240x240
        is inside the RAM and skews, which upstream's display_driver.py records.
        """
        columns, rows = (320, 240) if self.madctl & 0x20 else (240, 320)
        self.x1 = min(self.x1, columns - 1)
        self.y1 = min(self.y1, rows - 1)

    def finish_args(self):
        if self.cmd == CMD_CASET and len(self.args) >= 4:
            self.x0 = (self.args[0] << 8) | self.args[1]
            self.x1 = (self.args[2] << 8) | self.args[3]
        elif self.cmd == CMD_RASET and len(self.args) >= 4:
            self.y0 = (self.args[0] << 8) | self.args[1]
            self.y1 = (self.args[2] << 8) | self.args[3]
        elif self.cmd == CMD_MADCTL and self.args:
            self.madctl = self.args[0]
        elif self.cmd == CMD_COLMOD and self.args:
            self.colmod = self.args[0]
        self.args = []

    def data(self, payload):
        if self.cmd == CMD_RAMWR:
            self.pixels_in(payload)
        else:
            self.args.extend(payload)
            # CASET/RASET carry exactly four bytes and are acted on as soon as
            # they arrive, because RAMWR may follow in the same breath.
            if self.cmd in (CMD_CASET, CMD_RASET) and len(self.args) >= 4:
                cmd, self.cmd = self.cmd, self.cmd
                self.finish_args()
                self.cmd = cmd

    # ---- pixel writes ----

    def pixels_in(self, payload):
        self.pending.extend(payload)
        self.writes += len(payload)
        self.frame_bytes += len(payload)
        # A full-screen RAMWR is one finished screen. Keeping each one lets a
        # single boot walk a flow and hand back a picture per screen.
        if self.frame_bytes >= self.width * self.height * 2:
            self.frame_bytes = 0
            self._pending_snapshot = True
        # RGB565, high byte first: ST7789.py builds "BGR;16" then byteswaps.
        count = len(self.pending) // 2
        if not count:
            return
        chunk = self.pending[: count * 2]
        del self.pending[: count * 2]

        x, y = self.cursor if self.cursor else (self.x0, self.y0)
        for i in range(count):
            hi = chunk[i * 2]
            lo = chunk[i * 2 + 1]
            value = (hi << 8) | lo
            red = ((value >> 11) & 0x1F) * 255 // 31
            green = ((value >> 5) & 0x3F) * 255 // 63
            blue = (value & 0x1F) * 255 // 31
            if 0 <= x < self.width and 0 <= y < self.height:
                offset = (y * self.width + x) * 3
                self.pixels[offset] = red
                self.pixels[offset + 1] = green
                self.pixels[offset + 2] = blue
            x += 1
            if x > self.x1:
                x = self.x0
                y += 1
                if y > self.y1:
                    y = self.y0
        self.cursor = (x, y)
        if getattr(self, "_pending_snapshot", False):
            self._pending_snapshot = False
            self.frames.append(bytes(self.pixels))


# What the driver's init sets for the Waveshare 1.3" hat. MADCTL and inversion
# are calibration for how that glass is mounted, chosen so the app's upright
# canvas appears upright, so the decoder does not re-apply them geometrically:
# doing that would rotate every correct frame and hide the real fault. A wrong
# value is caught by comparing it, which is what EXPECTED_MADCTL is for.
EXPECTED_MADCTL = 0x70
# The 320x240 SeedSigner Plus panel, as st7789_mpy.py rotates it: 0x60 from its
# rotation table, plus the BGR bit its rotation() adds.
EXPECTED_MADCTL_320x240 = 0x68


def apply_madctl(panel):
    """Re-orient frame memory the way MADCTL says the panel scans it.

    Not called during normal decoding; kept for inspecting a frame whose MADCTL
    is not the expected one.
    """
    mx = bool(panel.madctl & 0x40)
    my = bool(panel.madctl & 0x80)
    mv = bool(panel.madctl & 0x20)
    width, height = panel.width, panel.height
    src = panel.pixels
    out = bytearray(len(src))
    for y in range(height):
        for x in range(width):
            sx, sy = x, y
            if mv:
                sx, sy = sy, sx
            if mx:
                sx = width - 1 - sx
            if my:
                sy = height - 1 - sy
            si = (sy * width + sx) * 3
            di = (y * width + x) * 3
            out[di:di + 3] = src[si:si + 3]
    panel.pixels = out


def replay(stream, width=240, height=240):
    panel = Panel(width, height)
    events = []
    offset = 0
    while offset + HEADER.size <= len(stream):
        dc, _cs, flags, length = HEADER.unpack_from(stream, offset)
        offset += HEADER.size
        payload = stream[offset:offset + length]
        offset += length
        if len(payload) < length:
            break

        if flags & FLAG_GPIO:
            line, value = payload[0], payload[1]
            if line == LINE_RST:
                events.append(f"reset line -> {value}")
            elif line == LINE_BL:
                events.append(f"backlight -> {value}")
            continue

        if dc == 0:
            for code in payload:
                panel.command(code)
        else:
            panel.data(payload)
    panel.finish_args()
    return panel, events


def write_png(path, width, height, rgb):
    """Minimal PNG writer, so the decoder needs nothing but the standard library."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        raw.extend(rgb[y * width * 3:(y + 1) * width * 3])

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(chunk(b"IHDR", header))
        handle.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        handle.write(chunk(b"IEND", b""))


# A whole signing flow decodes to about a hundred frames. Far more than that means
# something upstream hung and kept redrawing, and writing a PNG per frame then
# fills the disk: a probe that hung once wrote 30118 of them, 3.4 GB, and nothing
# stopped it. Cap the writing, keep counting, and say so loudly.
MAX_SPLIT_FRAMES = 500


def main():
    args = sys.argv[1:]
    width, height = 240, 240
    for arg in list(args):
        if arg.startswith("--panel="):
            width, height = (int(v) for v in arg[len("--panel="):].split("x"))
            args.remove(arg)

    if len(args) not in (2, 3):
        print("usage: decode_st7789.py capture.bin out.png [--split] [--panel=320x240]",
              file=sys.stderr)
        return 2

    with open(args[0], "rb") as handle:
        stream = handle.read()

    panel, events = replay(stream, width, height)

    if len(args) == 3 and args[2] == "--split":
        base = args[1][:-4] if args[1].endswith(".png") else args[1]
        for index, frame in enumerate(panel.frames[:MAX_SPLIT_FRAMES], 1):
            write_png(f"{base}-{index:02d}.png", panel.width, panel.height, frame)
        if len(panel.frames) > MAX_SPLIT_FRAMES:
            print(f"REFUSED: {len(panel.frames)} frames is far past the "
                  f"{MAX_SPLIT_FRAMES} this expects. Whatever produced this capture "
                  f"did not stop. Wrote the first {MAX_SPLIT_FRAMES} only.")
        else:
            print(f"wrote {len(panel.frames)} frame(s) as {base}-NN.png")
    else:
        write_png(args[1], panel.width, panel.height, panel.pixels)

    print(f"captured {len(stream)} bytes, {panel.writes} pixel bytes written")
    print(f"MADCTL 0x{panel.madctl:02X}  COLMOD 0x{panel.colmod:02X}  "
          f"inverted={panel.inverted}")
    expected = EXPECTED_MADCTL_320x240 if (width, height) == (320, 240) else EXPECTED_MADCTL
    if panel.madctl != expected:
        print(f"  WARNING: MADCTL is 0x{panel.madctl:02X}, expected "
              f"0x{expected:02X} — the screen would be rotated or mirrored")
    if not panel.inverted:
        print("  WARNING: inversion was never enabled — this panel shows a "
              "negative image without it")
    for event in events[:8]:
        print(f"  {event}")
    print(f"wrote {args[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
