"""Boot game on the Virtual HAT: Snake through the real ST7789 driver, then the unlock.

Runs inside the UML sandbox, the way start.sh runs it on the device, except that the
profile is set by hand (a sandbox has no /proc/device-tree/model) and os.execv is
recorded instead of replacing the process (the sandbox has no /opt/src).

Not wired into run_all.sh. Stage the app's seedsigner/ and boot-game/src/bootgame as
/app/seedsigner and /app/bootgame_pkg/bootgame, then run it like any other probe. Each held
key prints PRESS once; the pre-fix input.py printed it ~45 times per 2.5 s hold.

Verdicts printed:
  GAME RUNNING        the game loop is alive after the boot delay
  NEGATIVE OK         a wrong order (KEY2, KEY1, KEY3) did not hand off
  HANDOFF OK          KEY1, KEY2, KEY3 reached os.execv(/usr/bin/python3, main.py) in /opt/src
  GAME FAILED         the game raised; boot.py would fall back to the wallet
"""
import logging, os, sys, threading, time

sys.path.insert(0, "/app/bootgame_pkg")
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

from seedsigner.models.settings import Settings
Settings.RUNTIME_PROFILE = "rpi_40"
from seedsigner.models.settings_definition import SettingsConstants
Settings.get_instance().set_value(SettingsConstants.SETTING__DISPLAY_CONFIGURATION,
                                  SettingsConstants.DISPLAY_CONFIGURATION__ST7789__240x240)

BUTTONS = {"KEY1": 21, "KEY2": 20, "KEY3": 16}
handed_off = threading.Event()


# Long enough to span a frame draw plus Snake's 1.5 s game-over pause in the sandbox, where
# one frame takes ~0.35 s. A shorter hold can fall entirely between two polls.
HOLD = float(os.environ.get("HOLD", "2.5"))


def press(name, hold=None):
    hold = HOLD if hold is None else hold
    with open("/dev/ss_spicap", "wb", buffering=0) as h:
        h.write(bytes([BUTTONS[name], 0]))
        time.sleep(hold)
        h.write(bytes([BUTTONS[name], 1]))
    time.sleep(0.35)


chdir_seen = []
os.chdir = lambda p: chdir_seen.append(p)


def fake_execv(path, argv):
    print("EXECV", path, argv, "chdir", chdir_seen, flush=True)
    ok = path == "/usr/bin/python3" and argv[-1] == "main.py" and chdir_seen[-1:] == ["/opt/src"]
    print("HANDOFF OK" if ok else "HANDOFF WRONG TARGET", flush=True)
    handed_off.set()
    time.sleep(1)
    os._exit(0)


os.execv = fake_execv


def fingers():
    time.sleep(8)
    print("GAME RUNNING", flush=True)
    for k in ("KEY2", "KEY1", "KEY3"):
        press(k)
    time.sleep(3)
    print("NEGATIVE " + ("FAILED: wrong order handed off" if handed_off.is_set() else "OK"), flush=True)
    for k in ("KEY1", "KEY2", "KEY3"):
        press(k)
    time.sleep(20)
    if not handed_off.is_set():
        print("HANDOFF NEVER HAPPENED", flush=True)
        os._exit(1)


threading.Thread(target=fingers, daemon=True).start()

from bootgame import boot
import bootgame.input as _inp  # log every press the game receives
_orig = _inp.ButtonReader.presses
def _logged(self):
    for k in _orig(self):
        print("PRESS", k, flush=True)
        yield k
_inp.ButtonReader.presses = _logged
try:
    from bootgame.runner import run
    run()
except Exception:
    logging.exception("GAME FAILED")
boot.hand_off()
