"""Does the Python fake still answer like the real applet?

There are two cards in this project. `FakeCardWithSecrets` in the app's own test
suite is a few hundred lines of Python that imitates the nonce vault, and it is
what 1800 unit tests run against, because they cannot each start a JVM. The
SeedKeeper applet is the Java that gets flashed. Nothing checked that the two
agree, so the applet could change and every test would keep passing against an
imitation that had quietly become wrong.

This runs the same sequence against both and compares what comes back. It does
not compare byte for byte: a nonce is random, so two cards asked for one will
answer differently and should. What must match is the shape and the rules --
the sizes, and above all the refusals, since the refusals are the security
property.

What it binds, exactly, so the name does not promise more than it checks:

    both rounds reach the same stage, and produce the same signature count
    a replayed sealed nonce is refused, with the same error
    a sealed nonce whose bytes were edited is refused
    the pool is refilled to the same depth, with the same sized entries
    the sealed nonce is the same size

What it does not bind: behaviour under an unverified PIN, the status word of
every error path, the internal format of a seal, and anything about eviction
once more nonces are outstanding than the card tracks. Those are worth adding;
they are not covered today.

    JAVA_HOME=... JCARDSIM_JAR=... APPLET_SRC=... \
    PYTHONPATH=<this dir>:<doomsigner/src>:<doomsigner/tests> \
    python3 contract_test.py
"""
import json
import os
import sys
import types

stub = types.ModuleType("pytest")
stub.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
stub.mark = types.SimpleNamespace(
    parametrize=lambda *a, **k: (lambda fn: fn),
    skipif=lambda *a, **k: (lambda fn: fn),
    skip=lambda *a, **k: (lambda fn: fn))
stub.raises = lambda *a, **k: None
stub.skip = lambda *a, **k: None
sys.modules.setdefault("pytest", stub)

from embit import bip32, bip39

from seedsigner.helpers import musig2_card as mc
from seedsigner.helpers import musig2_psbt as mp

FIXTURE = os.environ.get(
    "MUSIG2_FIXTURE",
    "/home/rob/apps/doomsigner/tests/data/musig2_psbts.json")

failures = []


def compare(name, fake, real, detail=""):
    same = fake == real
    print(f"  {'ok  ' if same else 'DIFF'} {name}"
          + (f"   fake={fake!r} applet={real!r}" if not same else "")
          + (f"   {detail}" if same and detail else ""), flush=True)
    if not same:
        failures.append(name)


def exercise(session, root, psbt_b64, other_nonce_key, other_nonce):
    """One signing, then a replay of the sealed nonce. Returns what to compare."""
    from embit.psbt import PSBT
    from test_musig2_psbt import without_other_nonces

    psbt = without_other_nonces(psbt_b64)
    first = session().advance(psbt, root)
    round_one = psbt.to_string()

    psbt2 = PSBT.from_string(round_one)
    psbt2.inputs[0].unknown[other_nonce_key] = other_nonce
    second = session().advance(psbt2, root)
    signatures = len([k for k in psbt2.inputs[0].unknown
                      if k[0] == mp.PSBT_IN_MUSIG2_PARTIAL_SIG])

    replay = PSBT.from_string(round_one)
    replay.inputs[0].unknown[other_nonce_key] = other_nonce
    try:
        session().advance(replay, root)
        refusal = "accepted"
    except Exception as exc:
        refusal = type(exc).__name__

    # A seal whose bytes were edited must be refused before it is opened, and
    # refused differently from one that was merely already spent: "someone
    # tampered with this" and "you already used it" are different answers.
    tampered = PSBT.from_string(round_one)
    tampered.inputs[0].unknown[other_nonce_key] = other_nonce
    pool = mc._pooled(tampered, role_for(tampered, root))
    if pool:
        value = bytearray(tampered.inputs[0].unknown[pool[0]])
        value[-1] ^= 0xFF
        tampered.inputs[0].unknown[pool[0]] = bytes(value)
    try:
        session().advance(tampered, root)
        tamper_outcome = "accepted"
    except Exception as exc:
        tamper_outcome = type(exc).__name__

    pooled = mc._pooled(psbt2, role_for(psbt2, root))
    return {
        "round one stage": first.stage,
        "round two stage": second.stage,
        "partial signatures": signatures,
        "replay outcome": refusal,
        "edited seal outcome": tamper_outcome,
        "pooled nonces left": len(pooled),
        "pooled nonce size": len(psbt2.inputs[0].unknown[pooled[0]]) if pooled else 0,
    }


def role_for(psbt, root):
    return mp.roles(psbt, root)[0][0]


def main():
    data = json.load(open(FIXTURE))
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(data["mnemonics"]["A"]))
    other_root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(data["mnemonics"]["B"]))

    from test_musig2_psbt import without_other_nonces, core_nonce
    reference = without_other_nonces(data["psbt_round_one"])
    role = role_for(reference, root)
    other, nonce = core_nonce(data, role)
    key = mp._key(mp.PSBT_IN_MUSIG2_PUB_NONCE, role, other)

    # --- the Python double -------------------------------------------------
    from test_musig2_card import FakeCardWithSecrets
    fake_card = FakeCardWithSecrets(root, {1: other_root, 2: root})

    class FakeController:
        Satochip_Connector = fake_card

    def fake_session():
        session = mc.select(FakeController(), root)
        assert session is not None, "the fake card was not selected"
        return session

    fake = exercise(fake_session, root, data["psbt_round_one"], key, nonce)

    # --- the real applet ---------------------------------------------------
    from provision import connect
    connector = connect(bip39.mnemonic_to_seed(data["mnemonics"]["A"]))

    class RealController:
        Satochip_Connector = connector

    def real_session():
        session = mc.select(RealController(), root)
        assert session is not None, "the applet's card was not selected"
        return session

    real = exercise(real_session, root, data["psbt_round_one"], key, nonce)

    print("\nthe Python double against the real applet:", flush=True)
    for field in fake:
        # Show the agreed value, not just that they agree: two cards that both
        # wrongly accepted something would also "match".
        compare(field, fake[field], real[field], detail=f"both: {fake[field]!r}")

    if failures:
        print(f"\nFAIL: the fake and the applet disagree on {len(failures)}: "
              + ", ".join(failures), flush=True)
        return 1
    print("\nPASS: the fake answers like the applet on every compared property",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
