"""Sandbox fingers for the whole DOOM chain, as start.sh runs it.

boot game chooser -> joystick click -> execv DOOM -> play -> wrong order (control)
-> KEY1, KEY2, KEY3 -> execv the wallet. Follows the one pid through every execv.
"""
import os
import time

LINES = {"UP": 6, "DOWN": 19, "LEFT": 5, "RIGHT": 26, "PRESS": 13,
         "KEY1": 21, "KEY2": 20, "KEY3": 16}
T0 = time.monotonic()


def log(*parts):
    print(f"[{time.monotonic() - T0:6.1f}s]", *parts, flush=True)


def press(name, hold=0.6):
    with open("/dev/ss_spicap", "wb", buffering=0) as handle:
        handle.write(bytes([LINES[name], 0]))
        time.sleep(hold)
        handle.write(bytes([LINES[name], 1]))
    log("pressed", name)
    time.sleep(0.5)


def state(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            argv = [a.decode() for a in handle.read().split(b"\0") if a]
        return argv, os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def find_boot_pid():
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            found = state(pid)
            if found and "bootgame.boot" in found[0]:
                return int(pid)
    return None


def wait_for(pid, predicate, seconds, what):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        found = state(pid)
        if found and predicate(*found):
            log("REACHED", what, found)
            return True
        time.sleep(0.2)
    log("NEVER REACHED", what, state(pid))
    return False


pid = None
for _ in range(300):
    pid = find_boot_pid()
    if pid:
        break
    time.sleep(0.2)
log("boot game pid", pid, state(pid))

time.sleep(20)  # the chooser needs the app's imports, which are slow under UML
log("before click", state(pid))
press("PRESS")
is_doom = lambda argv, cwd: argv[0] == "/usr/local/games/doom"
doom_ok = wait_for(pid, is_doom, 30, "DOOM via execv, same pid")

time.sleep(6)  # title and demo
for key in ("KEY3", "KEY2", "KEY2", "KEY2"):  # menu, new game, episode, skill
    press(key)
    time.sleep(0.8)
time.sleep(3)
press("UP", hold=1.5)
press("PRESS")
press("LEFT", hold=0.8)
press("PRESS")
time.sleep(2)

for key in ("KEY2", "KEY1", "KEY3"):  # wrong order: must not unlock
    press(key)
time.sleep(3)
still = state(pid)
log("NEGATIVE " + ("OK" if still and is_doom(*still) else "FAILED"), still)

for key in ("KEY1", "KEY2", "KEY3"):
    press(key)
is_wallet = lambda argv, cwd: argv[-1] == "main.py" and cwd == "/opt/src"
wallet_ok = wait_for(pid, is_wallet, 30, "wallet via execv, same pid")

time.sleep(45)  # let the wallet draw its first screens
log("wallet still alive" if state(pid) else "wallet process GONE", state(pid))
log("VERDICT", "PASS" if doom_ok and wallet_ok else "FAIL")
