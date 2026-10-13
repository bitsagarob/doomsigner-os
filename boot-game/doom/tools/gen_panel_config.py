#!/usr/bin/env python3
"""
Generates the panel configuration header from SeedSigner's own source.

Transcribing pins and init sequences by hand is how a port ends up with a black
screen, so nothing here is hand written. Two sources are supported:

  * forks carrying hardware/io_config.json, which describes pins per board
  * upstream, where the pins are literals in ST7789.py

Only display configurations that have actually been checked are emitted. Anything
else is refused loudly rather than guessed at, because a plausible-looking wrong
init sequence is worse than a build error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# BOARD numbering (RPi.GPIO) to BCM, for upstream where pins are literals.
BOARD_TO_BCM = {3: 2, 5: 3, 7: 4, 8: 14, 10: 15, 11: 17, 12: 18, 13: 27, 15: 22,
                16: 23, 18: 24, 19: 10, 21: 9, 22: 25, 23: 11, 24: 8, 26: 7,
                29: 5, 31: 6, 32: 12, 33: 13, 35: 19, 36: 16, 37: 26, 38: 20, 40: 21}

# display config -> (driver file, width, height, parser). Only what we have verified.
SUPPORTED = {
    "st7789_240x240": ("ST7789.py", 240, 240, "calls"),
    "st7789_320x240": ("st7789_mpy.py", 320, 240, "table"),
}

BUTTONS = ("KEY_UP", "KEY_DOWN", "KEY_LEFT", "KEY_RIGHT", "KEY_PRESS", "KEY1", "KEY2", "KEY3")


def parse_init_sequence(driver: Path):
    """Pull the (command, [data]) sequence out of the driver's init()."""
    source = driver.read_text()
    body = source[source.index("def init(self)"):]
    end = body.find("def ", 4)
    body = body[:end] if end > 0 else body

    # Commented-out alternatives sit on the same lines as live calls.
    body = re.sub(r"#.*", "", body)

    sequence = []
    for call, value in re.findall(r"self\.(command|data)\(0x([0-9A-Fa-f]{2})\)", body):
        if call == "command":
            sequence.append((int(value, 16), []))
        else:
            if not sequence:
                raise SystemExit("init() starts with data before any command")
            sequence[-1][1].append(int(value, 16))

    if not sequence:
        raise SystemExit(f"no init sequence found in {driver}")

    return sequence


def parse_init_table(driver: Path):
    """
    Pull the sequence out of a driver that holds it as a table of
    (command, data, delay) tuples rather than as command()/data() calls.
    """
    source = driver.read_text()
    body = source[source.index("_ST7789_INIT_CMDS = ("):]
    body = body[: body.index("\n)")]
    body = re.sub(r"#.*", "", body)

    sequence = []
    for command, data, delay in re.findall(
        r"\(\s*b'([^']*)'\s*,\s*b'([^']*)'\s*,\s*(\d+)\s*\)", body
    ):
        decode = lambda text: [int(v, 16) for v in re.findall(r"\\x([0-9a-fA-F]{2})", text)]
        commands = decode(command)
        if len(commands) != 1:
            raise SystemExit(f"expected one command byte, got {commands}")
        sequence.append((commands[0], decode(data), int(delay)))

    if not sequence:
        raise SystemExit(f"no init table found in {driver}")

    return sequence


def parse_rotation(driver: Path, width: int, height: int):
    """
    Find the MADCTL value that gives the landscape orientation we want.

    The panel is natively portrait, so the controller is told to rotate as it
    scans out. Doing it here costs nothing per frame, where rotating the
    framebuffer in software would cost a transposed copy of every pixel.
    """
    source = driver.read_text()
    table = source[source.index(f"_DISPLAY_{height}x{width} = ("):]
    table = table[: table.index("\n)")]

    # rotation() ORs the colour order bit into the table's value. Missing it
    # swaps red and blue against the wallet, on the same panel.
    order = set(re.findall(r"^\s*color_order\s*=\s*(RGB|BGR)\s*$", source, re.M))
    if len(order) != 1:
        raise SystemExit(f"cannot tell which colour order {driver} uses")
    colour_bit = 0x08 if order == {"BGR"} else 0x00

    for madctl, w, h, xstart, ystart, swap in re.findall(
        r"\(\s*(0x[0-9a-fA-F]+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(True|False)\s*\)", table
    ):
        if int(w) == width and int(h) == height:
            if int(xstart) or int(ystart):
                raise SystemExit(f"rotation needs offsets {xstart},{ystart}; not handled yet")
            if swap == "True":
                raise SystemExit("rotation needs a byte swap; not handled yet")
            return int(madctl, 16) | colour_bit

    raise SystemExit(f"no {width}x{height} rotation found in {driver}")


def pins_from_io_config(app: Path, profile: str):
    """Forks that ship io_config.json describe pins per board."""
    config = json.loads((app / "src/seedsigner/hardware/io_config.json").read_text())

    for model in config.get("models", []):
        if model.get("shortname") == profile:
            display = model["display"]
            return (
                {name: display[name][1] for name in ("dc", "rst", "bl")},
                {name: model["buttons"][name][1] for name in BUTTONS},
                display.get("spi_bus", 0),
                display.get("spi_device", 0),
            )

    available = [m.get("shortname") for m in config.get("models", [])]
    raise SystemExit(f"unknown profile {profile}; io_config.json has {available}")


def pins_from_source(app: Path):
    """Upstream keeps the pins as BOARD-numbered literals."""
    driver = (app / "src/seedsigner/hardware/displays/ST7789.py").read_text()
    buttons = (app / "src/seedsigner/hardware/buttons.py").read_text()

    display = {}
    for name, attribute in (("dc", "_dc"), ("rst", "_rst"), ("bl", "_bl")):
        found = re.search(rf"self\.{attribute}\s*=\s*(\d+)", driver)
        if not found:
            raise SystemExit(f"cannot find {attribute} in ST7789.py; use --profile with a fork")
        display[name] = BOARD_TO_BCM[int(found.group(1))]

    # The 40-pin block comes first in that file, which is what a Pi Zero uses.
    block = buttons[buttons.index("class HardwareButtonsConstants"):]
    mapping = {}
    for name in BUTTONS:
        found = re.search(rf"^\s+{name}\s*=\s*(\d+)", block, re.MULTILINE)
        if not found:
            raise SystemExit(f"cannot find {name} in buttons.py")
        mapping[name] = BOARD_TO_BCM[int(found.group(1))]

    return display, mapping, 0, 0


def render(display_config, width, height, display_pins, buttons, spi_bus, spi_device, sequence, app, profile, madctl, repeat):
    lines = [
        "/*",
        " * GENERATED by tools/gen_panel_config.py. Do not edit.",
        " *",
        f" * source:  {app}",
        f" * profile: {profile or 'parsed from source (upstream layout)'}",
        f" * display: {display_config}",
        " *",
        " * Everything here comes from SeedSigner's own driver and pin map, so it",
        " * cannot drift from the code that actually works on this hardware.",
        " */",
        "#ifndef SS_PANEL_CONFIG_H",
        "#define SS_PANEL_CONFIG_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define SS_PANEL_W {width}",
        f"#define SS_PANEL_H {height}",
        "",
        "/* Written after init so the controller rotates as it scans out. 0 = none. */",
        f"#define SS_PANEL_MADCTL 0x{madctl:02X}" if madctl is not None else "#define SS_PANEL_MADCTL 0",
        "",
        "/* Some drivers run the init sequence more than once; match them. */",
        f"#define SS_PANEL_INIT_REPEAT {repeat}",
        "",
        f'#define SS_SPI_DEVICE "/dev/spidev{spi_bus}.{spi_device}"',
        "",
        f"#define SS_PIN_DC  {display_pins['dc']}",
        f"#define SS_PIN_RST {display_pins['rst']}",
        f"#define SS_PIN_BL  {display_pins['bl']}",
        "",
    ]
    for name in BUTTONS:
        lines.append(f"#define SS_PIN_{name.replace('KEY_', '').replace('KEY', 'KEY')} {buttons[name]}")

    lines += [
        "",
        "typedef struct {",
        "    uint8_t command;",
        "    uint8_t length;",
        "    uint8_t data[16];",
        "    uint16_t delay_ms;",
        "} ss_panel_cmd_t;",
        "",
        "static const ss_panel_cmd_t SS_PANEL_INIT[] = {",
    ]
    for entry in sequence:
        command, data = entry[0], entry[1]
        delay = entry[2] if len(entry) > 2 else 0
        values = ", ".join(f"0x{v:02X}" for v in data) or "0"
        lines.append(f"    {{ 0x{command:02X}, {len(data)}, {{ {values} }}, {delay} }},")
    lines += [
        "};",
        "",
        "#define SS_PANEL_INIT_LEN (sizeof(SS_PANEL_INIT) / sizeof(SS_PANEL_INIT[0]))",
        "",
        "#endif /* SS_PANEL_CONFIG_H */",
        "",
    ]

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, type=Path, help="a SeedSigner checkout")
    parser.add_argument("--display", default="st7789_240x240")
    parser.add_argument("--profile", help="hardware profile, for forks with io_config.json")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    if args.display not in SUPPORTED:
        raise SystemExit(
            f"display {args.display} has not been verified. Supported: "
            f"{sorted(SUPPORTED)}. Add it here once its driver has been checked."
        )

    driver_name, width, height, parser = SUPPORTED[args.display]
    driver = args.app / "src/seedsigner/hardware/displays" / driver_name
    if not driver.is_file():
        raise SystemExit(f"no driver at {driver}")

    has_io_config = (args.app / "src/seedsigner/hardware/io_config.json").is_file()
    if has_io_config:
        display_pins, buttons, spi_bus, spi_device = pins_from_io_config(
            args.app, args.profile or "RPI_40"
        )
    else:
        if args.profile:
            raise SystemExit("--profile given but this checkout has no io_config.json")
        display_pins, buttons, spi_bus, spi_device = pins_from_source(args.app)

    if parser == "table":
        sequence = parse_init_table(driver)
        madctl = parse_rotation(driver, width, height)
        # "yes, twice, once is not always enough" says their driver.
        repeat = 2
    else:
        sequence = parse_init_sequence(driver)
        madctl = None
        repeat = 1

    header = render(args.display, width, height, display_pins, buttons,
                    spi_bus, spi_device, sequence, args.app,
                    args.profile if has_io_config else None, madctl, repeat)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(header)
    print(f"generated {args.out} ({len(sequence)} init commands, {width}x{height})", file=sys.stderr)


if __name__ == "__main__":
    main()
