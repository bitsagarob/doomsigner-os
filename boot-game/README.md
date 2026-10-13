# boot-game

The device boots into a game. Press **KEY1, KEY2, KEY3** and it hands off to
SeedSigner.

With more than one game installed a chooser appears first. It lists games only:
there is deliberately no menu entry for the wallet, because that would give the
whole thing away. The chooser confirms with the joystick click alone, leaving
the three side buttons free to spell the unlock sequence from anywhere.

## How it hooks in

`opt/rootfs-overlay/start.sh` is the last link in the boot chain. Upstream it
runs the wallet directly; here it runs this package instead, which runs the
wallet when the unlock sequence is entered:

```sh
PYTHONPATH=/usr/local/bootgame /usr/bin/python3 -m bootgame.boot &
```

That is the entire change to SeedSigner OS: one line. The wallet application
itself is untouched and is still cloned from upstream at build time.

The handoff is an `os.execv`, so this process is *replaced* by SeedSigner rather
than backgrounded. No game code remains in memory while the signing application
is handling keys. If the game raises for any reason, SeedSigner is launched
anyway: a broken easter egg must never stand between someone and their device.

## Layout

| Module | Needs a Pi | Purpose |
| --- | --- | --- |
| `keys.py` | no | Symbolic button names |
| `game.py` | no | Snake as a pure state machine |
| `unlock.py` | no | The unlock sequence detector |
| `display.py` | no (Pillow only) | Draws a frame onto a PIL canvas |
| `input.py` | **yes** | Maps GPIO channels onto `Key` |
| `boot.py` | **yes** | Game loop and the handoff |

SeedSigner's button module imports `RPi.GPIO` at module scope, so anything
importing it cannot even load on a development machine. Keeping the game logic
free of that import is what makes it testable off-device, and it means only two
modules need real hardware or the emulator.

## Adding a game

One entry in `catalog.py`, one module under `games/` exposing
`play(renderer, reader, unlock)`, and one test file. Nothing else in the package
needs to know the game exists.

Built-in games are imported only when chosen, so an unused or broken game costs
nothing at boot. If a game raises, the player goes back to the chooser rather
than the device bricking; with only one game installed it falls through to the
wallet instead. External games are a binary and replace the process outright,
which is the strongest isolation available.

An external game appears only when its binary is present, so a Snake-only image
shows no chooser at all and behaves exactly as it did before a second game
existed. That is the whole feature toggle: it follows what was built into the
image, with nothing to configure.

DOOM is registered at `/usr/local/games/doom`, and the `pi0-smartcard` image
builds it (`BR2_PACKAGE_DOOMGENERIC`, see `doom/`). With DOOM installed the
chooser appears and opens on DOOM. It is launched with `-iwad`, because
doomgeneric looks for its WAD only in the working directory, which is
`/opt/src` by then.

## Tests

Upstream configures no formatter or linter, so none is applied here. Its
coverage config omits the hardware modules, and the same split is used here:
the pure modules are tested, the two hardware-bound ones are not.

```sh
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD":/w -w /w \
  -e PYTHONPATH=/w/src -e HOME=/tmp python:3.12-slim \
  sh -c "pip install -q pytest pillow && python -m pytest -q"
```

## Browser harness

`web/` runs the real modules under Pyodide, so the game can be played and the
unlock exercised without a Pi. Pyodide ships Pillow, so `display.py` renders in
the browser too and the output is pixel-identical to the device.

```sh
python3 -m http.server 8899 --directory boot-game
# then open http://127.0.0.1:8899/web/
```

Arrow keys steer, KEY1/KEY2/KEY3 (or `1`, `2`, `3`) unlock.

`web/session.py` is the harness equivalent of `boot.py`, because `boot.py` and
`input.py` import RPi.GPIO and SeedSigner and cannot load under Pyodide. So the
harness covers the game, the unlock and the rendering, but *not* the button
polling loop or the `execv` handoff. Those are only exercised on real hardware.

## Emulator

`emulator/` runs the game against the desktop SeedSigner emulator, headless in a
container. The staged tree deliberately mirrors the device (app at `/opt/src`,
game at `/usr/local/bootgame`, a `/usr/bin/python3` for `execv` to find), so the
exact command line from `start.sh` is what gets exercised.

```sh
docker build -t bootgame-emulator boot-game/emulator/
./boot-game/emulator/test.sh
```

It asserts that entering KEY1/KEY2/KEY3 replaces the process with `main.py` at
the same pid, which is the `execv` handoff actually happening. Because the crash
fallback *also* ends in `main.py`, the test additionally requires that the game
did not fail, and `negative-control.sh` checks the unlock does not fire on its
own. Without both, a green handoff test would prove nothing.

The emulator lags upstream, so `run.sh` applies three small compatibility
patches to the staged copy. It is pinned against SeedSigner 0.8.7 for this
reason; `dev` has moved past what it supports.

## Building an image

`stage.sh` copies the runtime modules into the rootfs overlay. Tests and
packaging stay here and never ship to the device.

```sh
./boot-game/stage.sh
export BOARD_TYPE=pi0
SS_ARGS="--$BOARD_TYPE --app-branch=0.8.7" \
  docker compose up --force-recreate --build
```

Pinning the app commit keeps the emulator and the image on the same SeedSigner
build, so the game cannot work in one and break in the other.

## Note

An image built from this branch will not match the published SeedSigner
reproducible build hashes. It is a toy. Do not put a real seed on it.
