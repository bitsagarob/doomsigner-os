"""Demo 1 on the real applet: the nonce vault, both rounds, then a replay."""

# Run with PYTHONPATH=<this dir>:<doomsigner/src> and JCARDSIM_JAR, APPLET_SRC,
# JAVA_HOME set. See README.md.
import json, os, sys
from embit import bip32, bip39
from embit.psbt import PSBT
import types

# The app's test helpers import pytest at module scope, and these demos are not
# run by pytest. Stub the decorators rather than make a test runner a runtime
# dependency of a demo: it was installed here and absent on a CI runner, which
# is exactly the difference that should not decide whether this works.
_stub = types.ModuleType("pytest")
_stub.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
_stub.mark = types.SimpleNamespace(
    parametrize=lambda *a, **k: (lambda fn: fn),
    skipif=lambda *a, **k: (lambda fn: fn),
    skip=lambda *a, **k: (lambda fn: fn))
_stub.raises = lambda *a, **k: None
_stub.skip = lambda *a, **k: None
sys.modules.setdefault("pytest", _stub)

from provision import connect
from seedsigner.helpers import musig2_card as mc, musig2_psbt as mp

# Paths come from the environment so this runs on a CI runner as well as here.
# PYTHONPATH must already carry the app's src and tests; MUSIG2_FIXTURE names the
# psbt fixture.
FIXTURE = os.environ.get(
    "MUSIG2_FIXTURE",
    "/home/rob/apps/doomsigner/tests/data/musig2_psbts.json")
from test_musig2_psbt import without_other_nonces, role_of, core_nonce

data = json.load(open(FIXTURE))
root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(data['mnemonics']['A']))
cc = connect(bip39.mnemonic_to_seed(data['mnemonics']['A']))

class Ctl: Satochip_Connector = cc
def fresh():
    s = mc.select(Ctl(), root)
    assert s is not None, "card not selected"
    return s

psbt = without_other_nonces(data["psbt_round_one"])
p1 = fresh().advance(psbt, root)
round_one = psbt.to_string()
print(f"round 1: stage={p1.stage}", flush=True)

# power off, then round two on a session that never saw round one
psbt2 = PSBT.from_string(round_one)
role = role_of(psbt2, root)
other, nonce = core_nonce(data, role)
psbt2.inputs[0].unknown[mp._key(mp.PSBT_IN_MUSIG2_PUB_NONCE, role, other)] = nonce
p2 = fresh().advance(psbt2, root)
sigs = [k for k in psbt2.inputs[0].unknown if k[0] == mp.PSBT_IN_MUSIG2_PARTIAL_SIG]
print(f"round 2: stage={p2.stage}  partial signatures={len(sigs)}", flush=True)

# a copy of the sealed nonce, replayed
replay = PSBT.from_string(round_one)
replay.inputs[0].unknown[mp._key(mp.PSBT_IN_MUSIG2_PUB_NONCE, role, other)] = nonce
try:
    fresh().advance(replay, root)
    print("replay: ACCEPTED", flush=True)
except Exception as exc:
    print(f"replay refused by the applet: {type(exc).__name__}: {str(exc)[:70]}", flush=True)
