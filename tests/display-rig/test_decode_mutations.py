#!/usr/bin/env python3
"""Break the decoder on purpose, and check test_decode_st7789.py notices.

A test suite that has never been seen to fail proves nothing. Every check in
test_decode_st7789.py passed the first time it was run, which is exactly the
state a vacuous test is in, so each one is answered here by a change that should
break it.

It found two real gaps on its first run. A gpio event was only ever tested with
the data/command line low, so a decoder that ignored the gpio flag read those
two bytes as harmless commands and no check moved; on the device that line is
usually high and they would have been read as pixels. And inversion was only
checked before any pixel existed, so negating an all-black frame changed nothing
the check looked at.

Each mutation is applied to a throwaway copy. Nothing here edits the rig.

    python3 test_decode_mutations.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

RIG = os.path.dirname(os.path.abspath(__file__))
APP = os.environ.get("APP_SRC", "/home/rob/apps/doomsigner/src")

# (what is broken, which file, find, replace). A file named APP/... is in the
# app source tree rather than the rig.
MUTATIONS = [
    ("colour bytes swapped in the decoder", "decode_st7789.py",
     "            hi = chunk[i * 2]\n            lo = chunk[i * 2 + 1]",
     "            lo = chunk[i * 2]\n            hi = chunk[i * 2 + 1]"),
    ("a field added to the C record header", "ss_display_capture.c",
     "\tu16 flags;\t/* bit 0: this record is a gpio event, not a transfer */",
     "\tu16 flags;\t/* bit 0: this record is a gpio event, not a transfer */\n\tu32 seq;"),
    ("the C header stops being packed", "ss_display_capture.c",
     "\tu32 len;\n} __packed;", "\tu32 len;\n};"),
    ("the reset line number drifts", "decode_st7789.py",
     "LINE_DC, LINE_RST, LINE_BL = 25, 27, 24",
     "LINE_DC, LINE_RST, LINE_BL = 25, 17, 24"),
    ("the addressing window stops wrapping", "decode_st7789.py",
     "            if x > self.x1:", "            if x > self.width:"),
    ("a frame is never considered finished", "decode_st7789.py",
     "if self.frame_bytes >= self.width * self.height * 2:",
     "if self.frame_bytes >= self.width * self.height * 4:"),
    ("MADCTL is no longer recorded", "decode_st7789.py",
     "        elif self.cmd == CMD_MADCTL and self.args:\n"
     "            self.madctl = self.args[0]",
     "        elif self.cmd == CMD_MADCTL and self.args:\n            pass"),
    ("gpio records are decoded as pixels", "decode_st7789.py",
     "            continue\n\n        if dc == 0:", "            pass\n\n        if dc == 0:"),
    ("the PNG filter byte is wrong", "decode_st7789.py",
     "        raw.append(0)  # filter: none", "        raw.append(1)  # filter: none"),
    ("SWRESET no longer clears the panel", "decode_st7789.py",
     "        elif code == CMD_SWRESET:\n            self.__init__(self.width, self.height)",
     "        elif code == CMD_SWRESET:\n            pass"),
    ("the driver changes MADCTL", "APP/seedsigner/hardware/displays/ST7789.py",
     "        self.command(0x36)\n        self.data(0x70)",
     "        self.command(0x36)\n        self.data(0x00)"),
    ("inversion is applied instead of recorded", "decode_st7789.py",
     "            self.inverted = True\n        elif code == CMD_INVOFF:",
     "            self.inverted = True\n"
     "            self.pixels = bytearray(255 - b for b in self.pixels)\n"
     "        elif code == CMD_INVOFF:"),
]

IGNORE = shutil.ignore_patterns("__pycache__", "out", ".git")


def apply_and_run(name, target, find, replacement):
    with tempfile.TemporaryDirectory() as work:
        rig = os.path.join(work, "rig")
        shutil.copytree(RIG, rig, ignore=IGNORE)
        if target.startswith("APP/"):
            app = os.path.join(work, "app")
            shutil.copytree(APP, app, ignore=IGNORE)
            path = os.path.join(app, target[len("APP/"):])
        else:
            app = APP
            path = os.path.join(rig, target)

        source = open(path).read()
        if find not in source:
            # The code moved. That is a real failure: this mutation is no longer
            # testing anything, and silently passing would be the worst outcome.
            return "STALE", "the text to break is not in the file any more"
        open(path, "w").write(source.replace(find, replacement, 1))

        result = subprocess.run(
            [sys.executable, "test_decode_st7789.py"], cwd=rig,
            capture_output=True, text=True, env=dict(os.environ, APP_SRC=app))
        caught = [line.strip()[5:].split("   ")[0]
                  for line in result.stdout.splitlines()
                  if line.strip().startswith("FAIL ")]
        if result.returncode == 0:
            return "MISSED", "every check still passed"
        return "caught", ", ".join(caught)


def main():
    if not os.path.isdir(APP):
        print(f"SKIP: no app source at {APP} (set APP_SRC)")
        return 0
    bad = 0
    for mutation in MUTATIONS:
        verdict, detail = apply_and_run(*mutation)
        if verdict != "caught":
            bad += 1
        print(f"  {'ok  ' if verdict == 'caught' else verdict} {mutation[0]}\n"
              f"        {detail}", flush=True)
    print()
    if bad:
        print(f"FAIL: {bad} of {len(MUTATIONS)} mutations were not caught")
        return 1
    print(f"PASS: all {len(MUTATIONS)} mutations were caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
