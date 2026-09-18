<h1 align="center">DoomSigner</h1>

<p align="center">
  <em>A proof-of-concept SeedSigner for silent payments and payment names.<br>
  BIP-352 receive and spend, BIP-353 name validation, and DOOM.</em>
</p>

**A proof of concept, not a product.** Can an airgapped signer handle silent
payments and verify a payment name on-device? No released hardware wallet does
either.

**Try it in a browser**, no hardware needed:
<https://bitsaga.be/seedsigner-simulator/wallet.html?firmware=doomsigner>

---

## What is new here

The signing application is built from
**[our own fork](https://github.com/bitsagarob/doomsigner)** (branch `dev`), not
from upstream.

### BIP-352 silent payments

| | |
| --- | --- |
| **Show a payment address** | `Seeds > (seed) > Silent payments`. Derivation matches [SeedSigner#769](https://github.com/SeedSigner/seedsigner/pull/769) and is checked against that thread's published vectors |
| **Connect to Sparrow** | Privacy warning, the address to cross-check, then the `spscan…` watch key. That key detects every payment the seed will ever receive and can spend none, so it is never shown as text |
| **Spend** | A BIP-352 output is already tweaked, so it needs `(b_spend + tweak)`, not BIP-341 TapTweak. Ports [SeedSigner#949](https://github.com/SeedSigner/seedsigner/pull/949) |
| **Send** | BIP-375 PSBTv2. The device ECDH-derives the output and returns DLEQ proofs |
| **Sparrow 2.5+ PSBTs** | Read as they arrive. Sparrow sends BIP-376 PSBTv2, and embit parses `PSBT_IN_SP_TWEAK` and `PSBT_IN_SP_SPEND_BIP32_DERIVATION` natively, so nothing rewrites the PSBT on the way in |

Cryptography from [embit#145](https://github.com/diybitcoinhardware/embit/pull/145),
pinned by commit in **two** places that must agree: `requirements.txt` in the app
fork, which governs a desktop checkout, and `opt/external-packages/python-embit`
here, which governs the device image. `build.sh` deletes `requirements.txt` from
the rootfs, so the Buildroot pin is the one a real device gets. When the two
disagreed, the image shipped embit 0.8.0 and silent payments could not run at all.

**Proven on chain.** Signet [send](https://signet.bitsaga.be/api/tx-proof?txid=71fb91163c8723b9e2ffe7c53c2981258c736ae18338d69a9cd35a215ffc7fdf)
and [spend](https://signet.bitsaga.be/api/tx-proof?txid=8569ad815dfe481f1aa2e4881febdbfed5890f566f9f63a92e9a90681899b29a).
Mainnet, a real 10,000 sats to an `sp1` address:
[block 965081](https://mempool.space/tx/172705760a51fa109b4aeef6f8bfbfde177a46c50d035abdd508b3d430b7c1ed).

And end to end through the browser, driving the real UI: the simulator connects
to the companion as Sparrow would, scans the PSBT, signs, and broadcasts a spend
that confirms
[in block 91762](https://signet.bitsaga.be/api/tx-proof?txid=fab92cf4de9f30c9463d38f82a856718752d2f172f8b4dee5e2c2472ccc81ef7).
That run exercises this fork's own code, not a patch applied over somebody
else's build.

### BIP-353 payment names

A silent payment output appears nowhere else on chain, so no human can compare an
address. **Validating the name is the only check that exists.** That needs a
DNSSEC proof validated offline, which needs two things a signer lacks: a
pure-Python validator, and a clock.

Both are being built here:

- **The validator.** A pure-Python port of Matt Corallo's `dnssec-prover`,
  offered as [pydnssec-prover#2](https://github.com/alvroble/pydnssec-prover/pull/2)
  (delegation walk plus an NSEC/NSEC3 engine, 37/37 against the Rust reference).
  It feeds [embit#102](https://github.com/diybitcoinhardware/embit/pull/102), which
  is what [SeedSigner#798](https://github.com/SeedSigner/seedsigner/pull/798) is
  waiting on. Review notes are
  [on #102](https://github.com/diybitcoinhardware/embit/pull/102#issuecomment-5517572376).
- **A 44-case differential corpus** answered by a Rust oracle wrapping
  `dnssec-prover` 0.6.10: 32 live valid proofs, 7 negatives, and the 5 BIP-353
  examples including 2 invalid on purpose. RRSIG windows are recorded per case,
  because proofs expire.
- **The clock.** ShieldSigner already reads a GoPro Labs precision-time QR
  ([3rdIteration#143](https://github.com/3rdIteration/seedsigner/pull/143)), so
  <https://silentpayments.net/timecode> draws one and this image reads it
  unchanged. Scan it **off a second device**, never off the machine that built
  the transaction, or one attacker supplies both the proof and the date that
  makes it look valid.

On-device validation is **not wired into the wallet yet**. Everything above it is.

Related: the Security Considerations section BIP-353 lacked, proposed as
[bitcoin/bips#2272](https://github.com/bitcoin/bips/pull/2272).

### Bugs this fork fixes

Found by building the thing and driving it, not by reading. The first two are
**not silent-payments specific** and affect any taproot key-path signing:

| Fix | What went wrong |
| --- | --- |
| **A taproot key-path signature was counted as no signature** | `PSBTParser.sig_count()` looked at `final_scriptwitness` or `partial_sigs`. A key-path signature is neither: it lives in `PSBT_IN_TAP_KEY_SIG`. A signer that fills that field and leaves finalising to the coordinator, which is what BIP-174 asks for, was reported as having signed nothing |
| **...and `trim()` then threw it away** | The worse half. `trim()` rebuilt the PSBT keeping only `final_scriptwitness` or `partial_sigs`, so even once counted, the PSBT handed back to the coordinator carried no signature at all |
| **A BIP-376 spend input looked like somebody else's** | `has_matching_input_fingerprint()` checked `bip32_derivations` and `taproot_bip32_derivations`. A silent payment output is taproot but its key is derived, so the spend key's origin sits in `PSBT_IN_SP_SPEND_BIP32_DERIVATION`. embit would have signed the PSBT; only the wallet's own routing could not see the input, and told the user to pick a different seed |
| **A missing optional dependency bricked the device** | `silent_payments.py` imported embit's BIP-352 modules at module scope and `seed_views` imports it at module scope, so an image whose embit predates BIP-352 could not import `seed_views` at all and the wallet would not start. Now late-imported behind `is_available()`: the worst case is a device that boots without a Silent payments menu |

Two more, outside the wallet:

- **The image shipped a pure-Python secp256k1 and said nothing.** embit no longer
  ships prebuilt binaries (its package excludes `*.so` outright), and its ctypes
  wrapper falls back to `util/py_secp256k1.py` in silence. On a Pi Zero that is a
  wallet that appears to work and takes minutes to sign. `libsecp256k1` is now a
  real Buildroot package, built with the four modules embit actually binds
  (`schnorrsig`, `extrakeys`, `ecdh`, `recovery`), and embit finds it because it
  queries the system loader before any bundled artifact.
- **The companion built a PSBT no standard defines.** It emitted PSBTv0 with the
  BIP-376 tweak smuggled through an unknown `0x20` field, which only worked while
  the simulator patched its own parser in at runtime. It now emits real PSBTv2, so
  the test path and the bytes Sparrow 2.5+ actually sends are the same thing.

---

## The game, and how to reach the wallet

**The device and the browser simulator behave differently. That is a divergence,
not a design.**

| | Boots into | To the wallet | Back to the game |
| --- | --- | --- | --- |
| **Browser simulator** | **DOOM**, always | **Five taps** on the top side button (KEY1) | **Five more taps** |
| **Real device** | **Snake** | **KEY1, KEY2, KEY3** in order | Not possible. Reboot |

**DOOM does not ship on hardware yet.** The buildroot package and the C port both
exist, but `BR2_PACKAGE_DOOMGENERIC` is enabled in no defconfig and only `opt/pi0`
offers it, which is not among the boards CI builds. So the device finds one game,
skips the chooser and boots Snake. The browser DOOM is a separate WebAssembly
build that never runs the Python boot game.

Every device handoff is `execv`, so the game is **replaced** rather than
backgrounded and no game code is in memory while the wallet holds keys. Hence no
way back without a reboot.

## What this is not

Not a security product, not audited, not a SeedSigner release.

**The signing application is modified**, so:

- **This image cannot reproduce an upstream SeedSigner release hash.** It is not
  meant to. `docs/building.md` describes upstream's check, not this one.
- **None of the work above has been reviewed by anyone.** Every upstream pull
  request it builds on is open and unmerged.

Use a real SeedSigner release for anything holding real bitcoin.

## Where things are

| Path | What |
| --- | --- |
| `boot-game/` | Chooser, unlock sequence, handoff |
| `boot-game/doom/` | The doomgeneric port, plus its wasm and device builds |
| `opt/build.sh` | Defaults to our app fork; `--app-repo` / `--app-branch` override |
| `opt/rootfs-overlay/start.sh` | The one line that launches the boot game, guarded so the wallet starts anyway if it fails |
| `opt/` | SeedSigner OS itself, from upstream |

---

Everything below this line is upstream's own README, kept as it was, and it is
still how the images are built.

---

<p align="center">
  <a href="https://seedsigner.com/">
    <img alt="Gitea" src="docs/img/logo.png" width="90"/>
  </a>
</p>
<h1 align="center">SeedSigner OS</h1>

<p align="center">
  <a href="https://opensource.org/licenses/MIT" title="License: MIT">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg">
  </a>
  <a href="" title="Twitter">
  <img src="https://img.shields.io/twitter/follow/seedsigner?style=social">
  </a>
</p>

* [Overview](#overview)
* [Building](docs/building.md)
* [Building (without Docker)](docs/without_docker.md)
* [SeedSigner OS structure](docs/structure.md)
* [Dev workflow](docs/dev_workflow.md)
* [Customizing Buildroot](docs/customize_buildroot.md)

<br/>

JUMP STRAIGHT TO: [🔥🔥🔥🛠 Quickstart: SeedSigner Reproducible Build! 🛠🔥🔥🔥](docs/building.md)

<br/>

---

# Overview

A custom linux based operating system built to manage software running on airgapped Bitcoin signing device. SeedSigner is both the project name and [application](http://github.com/SeedSigner/seedsigner/) running on airgapped hardware. This custom operating system, like all operating systems, manages the hardware resources and provides them to the application code. It's currently designed to run on common Raspberry Pi hardware with [accessories](https://github.com/SeedSigner/seedsigner/#shopping-list). The goal of SeedSigner OS is to provide an easy, fast, and secure way to build microSD card image to securely run [SeedSigner](https://seedsigner.com) code.


## ⚙️ Under the Hood

SeedSigner OS is built using [Buildroot](https://www.buildroot.org). Buildroot is a simple, efficient and easy-to-use tool to generate embedded Linux systems through cross-compilation. SeedSigner OS does not fork Buildroot, but uses Buildroot with custom configurations to build microSD card images tailor made for running SeedSigner.


## 🛂 Security

SeedSigner OS is built to reduce the attack surface area and enable additional application functionality. The OS is an order of magnitude smaller in size than Raspberry Pi OS (which is what typically is used to run software on a Pi device). Here are a list of some security and functional advantages of using SeedSigner OS.

- Boots 100% from RAM. This means, once you see the SeedSigner splash screen, you can remove the microSD card because no disk I/O is needed after boot!
- One FAT32 partition on the microSD card
- Removes these standard Raspberry Pi OS Kernel modules:
   - Networking and Bluetooth
   - SWAP
   - I2C
   - Serial
   - USB
   - Pulse-Width Modulation (PWM)
- NO HDMI support
- NO Serial connection TTL support
- NO Software supporting any wireless or networking chips
- A single read only zImage file on the boot partition containing the entire Linux kernel and filesystem


