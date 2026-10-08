# The Virtual HAT — screenshots from the real driver, with no Raspberry Pi

The kernel module pretends to be the Waveshare 1.3" hat: SPI display, data/command
line, buttons on the right pins. The app cannot tell the difference, and we record
what it sends. It is a hat, not a Pi, which is also the shortest way to say what it
cannot tell you.

SeedSigner is its own display driver. `ST7789.py` opens `/dev/spidev0.0`, sets
40 MHz, and pushes RGB565 down the wire while toggling a data/command line.
Nothing in the kernel knows a screen is attached. That is why the desktop mode
and the browser simulator cannot catch a display bug: both **replace** the
driver, so the code that actually talks to the panel never runs.

This rig runs that code unmodified and records what it puts on the wire.

Bugs it can see, all of which this codebase has shipped before:

* colour bytes in the wrong order (the panel wants RGB565 big endian)
* a wrong MADCTL, which rotates or mirrors the screen
* inversion never enabled, which shows a negative image
* a wrong addressing window, which shears or clips the frame
* text drawn outside the 240×240 panel

## Safety: this never touches the host kernel

The module is loaded inside **User-Mode Linux**, a Linux kernel that runs as an
ordinary userspace process. A panic there kills one process. That is not a
theory: it was proven by crashing the sandbox on purpose, twice, and confirming
the host's boot time, taint flag, module count and running services were
unchanged. Never `insmod` this on a real machine — it registers an SPI
controller and a gpiochip that pretend to be hardware.

## Layout

| File | What it is |
|---|---|
| `ss_display_capture.c` | kernel module: fake SPI controller + 32-line gpiochip, records every message |
| `decode_st7789.py` | replays a capture through the panel's state machine, writes a PNG (standard library only) |
| `Makefile` | out-of-tree build against a UML kernel tree |
| `flow_walk.py` | drives a signing flow inside the sandbox and captures every screen |
| `run_all.sh` | one full pass: every probe and shooter, both layers, into a dated directory |
| `release_gate.sh` | the check before tagging: runs a pass and refuses to call it good if anything is missing |
| `test_decode_st7789.py` | the decoder against hand-built captures, no sandbox needed |
| `test_decode_mutations.py` | breaks the decoder twelve ways and requires each break to be caught |
| `shoot_bip353_screens.py` | renders the BIP-353 payment-name screens in every status |
| `shoot_one_screen.py` | the smallest possible probe: one stock screen through the real driver |
| `shoot_one_visit.py` | the happy path: a coordinator-armed spend that signs in one visit |
| `shoot_boot_game.py` | the boot game: Snake through the real driver, a wrong-order control, then KEY1, KEY2, KEY3 into the wallet handoff |

## Why the app needs no changes

The gpiochip exposes 32 lines numbered like a real 40-pin Pi header: D/C on 25,
RST on 27, backlight on 24, buttons on 5, 6, 19, 26 and so on. The app therefore
runs with its stock `io_config.json` and its own `RPI_40` profile. It cannot
tell it is not on hardware.

The one thing a harness must set is `Settings.RUNTIME_PROFILE = "rpi_40"`,
because the profile is normally detected from `/proc/device-tree/model`, which a
sandbox does not have.

## Capture format

Every record is `struct cap_header` — `dc`, `cs`, `flags`, `len` — followed by
`len` bytes. `flags` bit 0 marks a gpio event rather than an SPI transfer, whose
payload is `[line, level]`. `dc` carries the data/command level at the moment
the message was sent, which is the only thing separating an ST7789 command from
pixel data.

Press a button by writing two bytes, `[line, level]`, to `/dev/ss_spicap`.
Buttons idle high, matching the hat's pull-ups, so pressing means writing a 0.

## Walking a whole flow

`flow_walk.py` follows the app's own `Destination` chain while a thread taps
SELECT on the real gpiochip, so the screens and their order are the device's
rather than a script's idea of them. `decode_st7789.py --split` then writes one
PNG per completed frame.

A 2-of-3 MuSig2 round from `tests/data/musig2_psbts.json` walks as:

    PSBTOverviewView -> PSBTNoChangeWarningView -> PSBTMathView ->
    PSBTAddressDetailsView -> PSBTFinalizeView -> PSBTMusig2RoundView ->
    PSBTSignedQRDisplayView

68 frames. The QR at the end decodes out of the captured pixels with pyzbar
(`UR:CRYPTO-PSBT/1-54/...`), so a coordinator can read what the device displayed
without a camera pointed at anything.

`flow_walk.py normal` walks an ordinary spend from the corpus for comparison:

    PSBTOverviewView -> PSBTMathView -> PSBTAddressDetailsView ->
    PSBTChangeDetailsView -> PSBTFinalizeView -> PSBTSignedQRDisplayView

So MuSig2 costs one screen and one extra pass, and nothing else. The round
screen reads "Step 1 of 2 -- Not signed yet. Send this back, then scan it
again."; an ordinary spend reaches its QR the first time through.

## Injecting a card

Patch `seedkeeper_utils.init_satochip`, do not set `controller.Satochip_Connector`.
The card-offer screen calls `init_satochip` itself, so setting the connector no
longer reaches it and the session falls back to holding the nonce in memory
without saying so. That is a quiet failure: the flow completes, the screens are
identical, and only a counter on the fake card shows the card was never used.

## What it does not prove

It runs the app's Python on the host's CPU with the host's CPython. It is not a
Raspberry Pi: no ARMv6, no 512 MB ceiling, no real panel, no camera, no SPI
timing. Whether the physical panel accepts what the driver sends at 40 MHz over
real wiring is still a hardware question.

For the ARM userland question — whether the shipped image contains everything
the app imports — see `tests/test_image_userland.py`, which runs the built
image's own ARM Python.

## Two decoder decisions worth knowing

`MADCTL` and inversion are **recorded and checked, not simulated**. Both are
calibration for how this particular glass is mounted: the driver sets MADCTL
`0x70` and turns inversion on so that an upright canvas appears upright and
colours come out right. A decoder that re-applied them geometrically would
rotate every correct frame and report a negative for every good one. So the
decoder decodes frame memory as written and warns when either value is not what
the driver's init sets.

## What runs where

The rig has two halves and they cost very different amounts to run.

| | needs | when it runs |
|---|---|---|
| `test_decode_st7789.py`, `test_decode_mutations.py` | python | every push, in the `Display rig` workflow, about 3 seconds |
| the kernel module compile | a UML kernel tree | every push, cached, about 2 seconds after the first |
| the applet layer | a JVM and jcardsim | weekly and on demand, in the `Applet contract` workflow |
| a full pass with screens | the UML sandbox | by hand, and before every tag |

The split is not arbitrary. The decoder is the part that fails **silently**: a
byte-order slip or an off-by-one in the addressing window does not crash, it
writes a plausible PNG that is wrong, and any screenshot taken from it is wrong
in the same way. So it is checked on the commit that could cause it. Getting a
real screen needs a kernel and the app inside it, which no hosted runner will do
cheaply, so that stays here.

## Before tagging a release

    ./release_gate.sh /home/rob/apps/seedsigner-sp/src v0.13

`run_all.sh` answers *did a pass happen*. That is a different question from *is
this good enough to ship*, and it cannot answer the second: it prints
`display layer: ran` whether the probes proved anything or timed out, and exits 0
either way. The gate reads the pass it produced and requires each probe to have
said the thing it exists to say.

It refuses an app tree with uncommitted changes, a pass where the applet layer
was skipped, a probe that produced no verdict or left a `Traceback`, and a
capture that decoded to no screens. Give it a ref and it also refuses to run
against a tree that is not on that ref.

It writes `GATE.txt` into the pass directory and tags nothing. **It cannot look
at the screens.** A screen can render perfectly and still say the wrong thing --
"Two Steps" did -- so the last step is a human opening `flow-musig2/`, `offer/`
and `refusal/` and reading them.
