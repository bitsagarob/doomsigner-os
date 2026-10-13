#!/usr/bin/env python3
"""Does the decoder still read a capture the way the kernel module writes one?

The Virtual HAT has two halves that must agree on a wire format and on what an
ST7789 does with it. The kernel half only runs inside User-Mode Linux, which
needs a kernel tree and a sandbox, so it is a local tool. This half is ordinary
Python and can run anywhere, on every push, in a second.

That split matters because a decoder is the kind of code that fails silently. A
byte-order slip or an off-by-one in the addressing window does not raise: it
writes a PNG that looks plausible and is wrong, and the screenshots we publish
would be wrong with it. So the checks here are all exact comparisons against
values stated literally, never against a second copy of the decoder's own
arithmetic.

Two things are bound:

  the record format   parsed out of ss_display_capture.c and compared with the
                      struct the decoder unpacks, so a field added to the C
                      header cannot quietly shift every field after it
  the panel           addressing, colour, frame splitting and gpio events,
                      driven by hand-built records with known answers

What it does NOT cover: whether the module compiles, whether it registers a
working SPI controller, and whether the app's driver sends the right thing. Those
need the sandbox. Run ./run_all.sh for that.

    python3 test_decode_st7789.py
"""

import os
import re
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decode_st7789 as dec

MODULE_C = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "ss_display_capture.c")

# RGB565 values whose expansion to 8 bits per channel is exact, so the expected
# answer can be written down rather than recomputed with the decoder's own
# formula. A test that recomputes it would agree with any formula, including a
# wrong one.
PALETTE = {
    0xFFFF: (255, 255, 255),
    0x0000: (0, 0, 0),
    0xF800: (255, 0, 0),
    0x07E0: (0, 255, 0),
    0x001F: (0, 0, 255),
    0xFFE0: (255, 255, 0),
    0x07FF: (0, 255, 255),
    0xF81F: (255, 0, 255),
}

failures = []


def check(name, condition, detail=""):
    print(f"  {'ok  ' if condition else 'FAIL'} {name}"
          + (f"   {detail}" if detail else ""), flush=True)
    if not condition:
        failures.append(name)


# ---- building a capture by hand ------------------------------------------

def record(dc, payload, flags=0):
    return dec.HEADER.pack(dc, 0, flags, len(payload)) + bytes(payload)


def command(code, *args):
    out = record(0, [code])
    if args:
        out += record(1, args)
    return out


def gpio(line, level, dc=1):
    """A gpio event. `dc` is whatever the data/command line was at the time, and
    it is usually high, because the driver spends most of its time sending
    pixels. A decoder that ignored the flag would read these two bytes as
    pixels."""
    return record(dc, [line, level], flags=dec.FLAG_GPIO)


def window(x0, y0, x1, y1):
    return (command(dec.CMD_CASET, x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF)
            + command(dec.CMD_RASET, y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF))


def pixels(values):
    """RGB565 words, high byte first, the order ST7789.py puts on the wire."""
    payload = bytearray()
    for value in values:
        payload.append((value >> 8) & 0xFF)
        payload.append(value & 0xFF)
    return record(1, payload)


def pixel_at(panel, x, y):
    offset = (y * panel.width + x) * 3
    return tuple(panel.pixels[offset:offset + 3])


# ---- the record format ----------------------------------------------------

C_TYPES = {"u8": "B", "u16": "H", "u32": "I", "s8": "b", "s16": "h", "s32": "i"}


def check_record_format():
    """The C struct the module writes and the struct the decoder unpacks."""
    source = open(MODULE_C).read()
    match = re.search(r"struct cap_header\s*\{(.*?)\}\s*(__packed)?\s*;",
                      source, re.S)
    if not match:
        check("cap_header found in the module source", False)
        return
    body, packed = match.group(1), match.group(2)

    fields = re.findall(r"\b(u8|u16|u32|s8|s16|s32)\s+(\w+)\s*;", body)
    fmt = "<" + "".join(C_TYPES[kind] for kind, _ in fields)

    check("the module's header is __packed", packed == "__packed",
          "otherwise the compiler inserts padding the decoder does not skip")
    check("decoder struct matches the C struct", fmt == dec.HEADER.format,
          f"C: {fmt}   decoder: {dec.HEADER.format}")
    check("field order matches", [name for _, name in fields] == ["dc", "cs", "flags", "len"],
          f"C: {[name for _, name in fields]}")

    defines = dict((name, int(value, 0)) for name, value in
                   re.findall(r"#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)", source))
    for c_name, py_value, label in (
            ("LINE_DC", dec.LINE_DC, "data/command line"),
            ("LINE_RST", dec.LINE_RST, "reset line"),
            ("LINE_BL", dec.LINE_BL, "backlight line"),
            ("CAP_FLAG_GPIO", dec.FLAG_GPIO, "gpio-event flag")):
        check(f"{label} agrees ({c_name})", defines.get(c_name) == py_value,
              f"C: {defines.get(c_name)}   decoder: {py_value}")


# ---- the panel ------------------------------------------------------------

def check_full_frame():
    """A whole screen of known colours comes back byte for byte."""
    words = [list(PALETTE)[(x // 30 + y // 30) % len(PALETTE)]
             for y in range(240) for x in range(240)]
    stream = window(0, 0, 239, 239) + command(dec.CMD_RAMWR) + pixels(words)
    panel, _ = dec.replay(stream)

    expected = bytearray()
    for word in words:
        expected.extend(PALETTE[word])
    check("a full frame decodes byte for byte", bytes(panel.pixels) == bytes(expected),
          f"{panel.writes} pixel bytes")
    check("one full RAMWR is one finished frame", len(panel.frames) == 1,
          f"{len(panel.frames)} frame(s)")


def check_byte_order():
    """High byte first. A byteswap in the driver must change the picture."""
    correct = window(0, 0, 0, 0) + command(dec.CMD_RAMWR) + record(1, [0xF8, 0x00])
    swapped = window(0, 0, 0, 0) + command(dec.CMD_RAMWR) + record(1, [0x00, 0xF8])
    a, _ = dec.replay(correct)
    b, _ = dec.replay(swapped)
    check("0xF800 high byte first is red", pixel_at(a, 0, 0) == (255, 0, 0),
          f"{pixel_at(a, 0, 0)}")
    check("the swapped bytes are not red", pixel_at(b, 0, 0) != (255, 0, 0),
          f"{pixel_at(b, 0, 0)}")


def check_window_addressing():
    """Pixels land inside the window, wrap at its right edge, and nowhere else."""
    stream = window(100, 50, 109, 54) + command(dec.CMD_RAMWR) + pixels([0x07E0] * 12)
    panel, _ = dec.replay(stream)

    green = [(x, y) for y in range(240) for x in range(240)
             if pixel_at(panel, x, y) == (0, 255, 0)]
    expected = [(x, 50) for x in range(100, 110)] + [(100, 51), (101, 51)]
    check("12 pixels into a 10-wide window wrap onto the next row",
          sorted(green) == sorted(expected), f"{len(green)} lit")
    check("nothing outside the window was touched", len(green) == 12)


def check_window_wraps_to_top():
    """Past the bottom row the cursor returns to the top of the window."""
    stream = window(0, 0, 1, 1) + command(dec.CMD_RAMWR) + pixels(
        [0xF800, 0xF800, 0xF800, 0xF800, 0x001F])
    panel, _ = dec.replay(stream)
    check("overrunning the window wraps to its first pixel",
          pixel_at(panel, 0, 0) == (0, 0, 255), f"{pixel_at(panel, 0, 0)}")


def check_window_end_past_the_ram():
    """An end address past the controller's RAM ends the window at the RAM's edge."""
    words = [0x0000] * (320 * 240)
    words[320] = 0xF800  # first pixel of the second row
    stream = (command(dec.CMD_MADCTL, 0x60) + window(0, 0, 320, 240)
              + command(dec.CMD_RAMWR) + pixels(words))
    panel, _ = dec.replay(stream, 320, 240)
    check("st7789_mpy.py's 320x240 window, which ends at 320, draws upright",
          pixel_at(panel, 0, 1) == (255, 0, 0), f"{pixel_at(panel, 0, 1)}")

    # The same off-by-one inside the RAM is a real skew, so it must stay one.
    words = [0x0000] * (241 * 240)
    words[241] = 0xF800
    stream = (command(dec.CMD_MADCTL, 0x70) + window(0, 0, 240, 239)
              + command(dec.CMD_RAMWR) + pixels(words))
    panel, _ = dec.replay(stream)
    check("an end address inside the RAM is honoured, skew and all",
          pixel_at(panel, 0, 1) == (255, 0, 0), f"{pixel_at(panel, 0, 1)}")


def check_frame_split():
    """Two screens in one capture come back as two frames, in order."""
    first = window(0, 0, 239, 239) + command(dec.CMD_RAMWR) + pixels([0xF800] * (240 * 240))
    second = window(0, 0, 239, 239) + command(dec.CMD_RAMWR) + pixels([0x001F] * (240 * 240))
    panel, _ = dec.replay(first + second)
    check("two full screens are two frames", len(panel.frames) == 2,
          f"{len(panel.frames)}")
    if len(panel.frames) == 2:
        check("the first frame is the first screen",
              panel.frames[0][:3] == bytes((255, 0, 0)), f"{panel.frames[0][:3].hex()}")
        check("the second frame is the second screen",
              panel.frames[1][:3] == bytes((0, 0, 255)), f"{panel.frames[1][:3].hex()}")


def check_gpio_records():
    """A gpio event is an event, never pixel data."""
    stream = (window(0, 0, 239, 239) + command(dec.CMD_RAMWR)
              + pixels([0xF800])
              + gpio(dec.LINE_RST, 0) + gpio(dec.LINE_RST, 1)
              + gpio(dec.LINE_BL, 1, dc=0)
              + pixels([0x001F]))
    panel, events = dec.replay(stream)
    check("reset and backlight are reported",
          events == ["reset line -> 0", "reset line -> 1", "backlight -> 1"],
          f"{events}")
    check("a gpio payload is not counted as pixels", panel.writes == 4,
          f"{panel.writes} pixel bytes")
    check("the pixel after the gpio events is the next pixel",
          pixel_at(panel, 1, 0) == (0, 0, 255), f"{pixel_at(panel, 1, 0)}")


def check_init_values_recorded():
    """MADCTL, COLMOD and inversion are recorded, not applied."""
    stream = (command(dec.CMD_MADCTL, dec.EXPECTED_MADCTL)
              + command(dec.CMD_COLMOD, 0x05)
              + window(0, 0, 239, 239) + command(dec.CMD_RAMWR) + pixels([0xF800])
              + command(dec.CMD_INVON)
              # Any command ends the RAMWR stream, so the next pixel needs its
              # own window and RAMWR, exactly as the driver would send it.
              + window(1, 0, 239, 239) + command(dec.CMD_RAMWR) + pixels([0x001F]))
    panel, _ = dec.replay(stream)
    check("MADCTL is recorded", panel.madctl == dec.EXPECTED_MADCTL,
          f"0x{panel.madctl:02X}")
    check("COLMOD is recorded", panel.colmod == 0x05, f"0x{panel.colmod:02X}")
    check("inversion is recorded", panel.inverted is True)
    check("a pixel written before INVON is not negated",
          pixel_at(panel, 0, 0) == (255, 0, 0), f"{pixel_at(panel, 0, 0)}")
    check("a pixel written after INVON is not negated",
          pixel_at(panel, 1, 0) == (0, 0, 255), f"{pixel_at(panel, 1, 0)}")

    off, _ = dec.replay(command(dec.CMD_INVON) + command(dec.CMD_INVOFF))
    check("INVOFF turns the record back off", off.inverted is False)


def check_madctl_expectation_is_the_driver_s():
    """The value the decoder warns about is the one the app's driver sets."""
    app = os.environ.get("APP_SRC", "/home/rob/apps/seedsigner-sp/src")
    driver = os.path.join(app, "seedsigner", "hardware", "displays", "ST7789.py")
    if not os.path.exists(driver):
        print(f"  skip  MADCTL against the driver ({driver} not present)")
        return
    # The driver writes MADCTL as a command byte followed by a data byte:
    #     self.command(0x36)
    #     self.data(0x70)
    source = open(driver).read()
    found = re.findall(r"command\(\s*0x36\s*\)\s*\n\s*self\.data\(\s*(0x[0-9A-Fa-f]+)",
                       source)
    values = {int(v, 16) for v in found}
    check("the driver still sets the MADCTL the decoder expects",
          values == {dec.EXPECTED_MADCTL},
          f"driver: {[hex(v) for v in sorted(values)]}  decoder: "
          f"0x{dec.EXPECTED_MADCTL:02X}")


def check_swreset_clears():
    stream = (window(0, 0, 0, 0) + command(dec.CMD_RAMWR) + pixels([0xF800])
              + command(dec.CMD_SWRESET))
    panel, _ = dec.replay(stream)
    check("SWRESET throws the frame away", pixel_at(panel, 0, 0) == (0, 0, 0),
          f"{pixel_at(panel, 0, 0)}")


def check_truncated_capture():
    """A capture cut off mid-record stops, rather than crashing or hanging."""
    stream = window(0, 0, 239, 239) + command(dec.CMD_RAMWR) + pixels([0xF800] * 100)
    for cut in (len(stream) - 1, len(stream) - 50, dec.HEADER.size + 1, 3):
        try:
            panel, _ = dec.replay(stream[:cut])
        except Exception as exc:
            check(f"a capture cut at {cut} bytes decodes", False, f"{exc!r}")
            return
    check("a capture cut mid-record decodes without raising", True)


def png_pixels(path):
    """Read back a PNG this decoder wrote, using only the standard library."""
    blob = open(path, "rb").read()
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    offset, width, height, data = 8, 0, 0, b""
    while offset < len(blob):
        length, kind = struct.unpack_from(">I4s", blob, offset)
        payload = blob[offset + 8:offset + 8 + length]
        stored, = struct.unpack_from(">I", blob, offset + 8 + length)
        assert zlib.crc32(kind + payload) & 0xFFFFFFFF == stored, f"bad CRC on {kind}"
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack_from(">IIBB", payload, 0)
            assert (depth, colour) == (8, 2), "not 8-bit truecolour"
        elif kind == b"IDAT":
            data += payload
        offset += 12 + length
    raw = zlib.decompress(data)
    stride = width * 3 + 1
    out = bytearray()
    for y in range(height):
        row = raw[y * stride:(y + 1) * stride]
        assert row[0] == 0, "unexpected PNG filter"
        out.extend(row[1:])
    return width, height, bytes(out)


def check_png_round_trip():
    words = [list(PALETTE)[(x + y) % len(PALETTE)]
             for y in range(240) for x in range(240)]
    stream = window(0, 0, 239, 239) + command(dec.CMD_RAMWR) + pixels(words)
    panel, _ = dec.replay(stream)

    with tempfile.TemporaryDirectory() as workdir:
        path = os.path.join(workdir, "out.png")
        dec.write_png(path, panel.width, panel.height, panel.pixels)
        try:
            width, height, rgb = png_pixels(path)
        except Exception as exc:
            check("the PNG can be read back", False, f"{exc!r}")
            return

    check("the PNG is 240x240", (width, height) == (240, 240), f"{width}x{height}")
    check("the PNG holds exactly the decoded pixels", rgb == bytes(panel.pixels))


CHECKS = [
    ("the capture record format", check_record_format),
    ("a full frame", check_full_frame),
    ("colour byte order", check_byte_order),
    ("the addressing window", check_window_addressing),
    ("window overrun", check_window_wraps_to_top),
    ("an end address past the RAM", check_window_end_past_the_ram),
    ("frame splitting", check_frame_split),
    ("gpio events", check_gpio_records),
    ("init values", check_init_values_recorded),
    ("MADCTL against the driver", check_madctl_expectation_is_the_driver_s),
    ("software reset", check_swreset_clears),
    ("a truncated capture", check_truncated_capture),
    ("the PNG writer", check_png_round_trip),
]


def main():
    for title, function in CHECKS:
        print(f"\n{title}:", flush=True)
        function()
    print()
    if failures:
        print(f"FAIL: {len(failures)} check(s) failed: " + ", ".join(failures))
        return 1
    print("PASS: the decoder reads a capture the way the module writes one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
