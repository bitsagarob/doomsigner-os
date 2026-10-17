#!/usr/bin/env bash
# One command for a full Virtual HAT pass: every probe and every shooter,
# against whatever app source you point it at.
#
#   ./run_all.sh /home/rob/apps/doomsigner/src        # dev
#   ./run_all.sh /home/rob/apps/_scratch/ss-card/src     # a branch worktree
#
# Two layers, and a pass says which of them it ran:
#
#   display   the app's real driver on a real kernel SPI bus, inside UML
#   applet    the real SeedKeeper applet in jCardSim behind real pysatochip
#
# The applet layer needs JAVA_HOME, JCARDSIM_JAR and APPLET_SRC. Without them it
# is skipped loudly and the summary says the pass was partial, because a run that
# quietly covers less than it claims is worse than one that fails.
#
# Existed because a "full test" was several hand-run scripts, which meant it was
# run less often than it should have been and slightly differently each time.
#
# Results land in a dated directory: the probes' output as text, and every
# screen as a numbered PNG. Picking which frames are worth publishing is still a
# human step; this gets you to the point of choosing.
#
# Everything under the sandbox is build output. It is deleted and regenerated
# from this directory on every run, so nothing there is worth editing and a
# stale copy cannot survive to be tested by mistake. Change a probe here.
set -uo pipefail

APP_SRC="${1:-/home/rob/apps/doomsigner/src}"
UML="${UML_ROOT:-/home/rob/apps/_scratch/uml}"
RIG="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date -u +%Y%m%d-%H%M)"
OUT="$UML/out/pass-$STAMP"

[ -d "$APP_SRC/seedsigner" ] || { echo "no app source at $APP_SRC"; exit 1; }
[ -x "$UML/run.sh" ] || { echo "no UML sandbox at $UML (see README)"; exit 1; }

mkdir -p "$OUT"

# Say what is actually being tested, loudly. A worktree on a detached HEAD does
# not move when its branch does, so a pass against one silently reports today's
# date over yesterday's code. That is the kind of quiet wrongness this rig
# exists to catch, and it would be embarrassing to ship it in the rig itself.
REPO_DIR="$(cd "$APP_SRC/.." && pwd)"
GIT_DESC="$(git -C "$REPO_DIR" log --oneline -1 2>/dev/null || echo 'not a git tree')"
GIT_REF="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
GIT_DIRTY="$(git -C "$REPO_DIR" status --porcelain 2>/dev/null | wc -l)"

echo "app source : $APP_SRC"
echo "commit     : $GIT_DESC"
echo "ref        : $GIT_REF"
echo "results    : $OUT"
if [ "$GIT_REF" = "HEAD" ]; then
  echo
  echo "  !! detached HEAD: this tree will NOT follow its branch."
  echo "     A later pass here tests this same commit no matter what was pushed."
fi
if [ "$GIT_DIRTY" != "0" ]; then
  echo "  !! $GIT_DIRTY uncommitted file(s): this pass is not reproducible from the commit."
fi
echo

{ echo "app source : $APP_SRC"
  echo "commit     : $GIT_DESC"
  echo "ref        : $GIT_REF"
  echo "uncommitted: $GIT_DIRTY file(s)"; } > "$OUT/provenance.txt"

# The applet is as load-bearing as the app now, so it is named the same way. A
# provenance file that describes only some of what a pass depended on invites
# the reader to assume it describes all of it.
if [ -n "${APPLET_SRC:-}" ] && [ -d "${APPLET_SRC}/.git" ]; then
  APPLET_DESC="$(git -C "$APPLET_SRC" log --oneline -1 2>/dev/null)"
  APPLET_REF="$(git -C "$APPLET_SRC" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  APPLET_DIRTY="$(git -C "$APPLET_SRC" status --porcelain 2>/dev/null | wc -l)"
  echo "applet     : $APPLET_DESC"
  echo "applet ref : $APPLET_REF"
  [ "$APPLET_REF" = "HEAD" ] && echo "  !! applet tree is on a detached HEAD"
  [ "$APPLET_DIRTY" != "0" ] && echo "  !! applet tree has $APPLET_DIRTY uncommitted file(s)"
  { echo "applet     : $APPLET_DESC"
    echo "applet ref : $APPLET_REF"
    echo "applet uncommitted: $APPLET_DIRTY file(s)"; } >> "$OUT/provenance.txt"
fi

# Stage the app and the fixtures the probes read.
# Scratch is regenerated, never edited: the jcardsim pieces are staged from the
# repo too, so the sandbox holds no source anyone could change by accident.
STAGE="$UML/appsrc-pass"
rm -rf "$STAGE" "$UML/jcardfs"; mkdir -p "$STAGE/tests/data" "$UML/jcardfs"
cp -r "$RIG/jcardsim/smartcard" "$UML/jcardfs/"
cp "$RIG/jcardsim"/*.py "$UML/jcardfs/"
cp -r "$APP_SRC/seedsigner" "$STAGE/"
for f in test_musig2_card.py test_musig2_psbt.py test_musig2_sp.py psbt_testing_util.py; do
  cp "$(dirname "$APP_SRC")/tests/$f" "$STAGE/tests/" 2>/dev/null || true
done
cp "$(dirname "$APP_SRC")/tests/data/musig2_psbts.json" "$STAGE/tests/data/" 2>/dev/null || true
mkdir -p "$STAGE/tests/data/psbt_test_suite"
cp "$(dirname "$APP_SRC")/tests/data/psbt_test_suite/NORMAL-1_p2wpkh.psbt" \
   "$STAGE/tests/data/psbt_test_suite/" 2>/dev/null || true
cp "$RIG"/*.py "$STAGE/"

run_one() {   # name, script inside /app, whether to capture the display
  local name="$1" script="$2" capture="${3:-yes}"
  local init="$UML/rootfs/pass-$name-init.sh"
  head -21 "$UML/rootfs/pytest-init.sh" \
    | sed "s|/uml/appsrc |/uml/appsrc-pass |" > "$init"
  {
    [ "$capture" = yes ] && echo "cat /dev/ss_spicap > /out/$name.bin &"
    echo "sleep 1"
    echo "cd /app && timeout 240 python3 /app/$script 2>&1 | grep -vE 'font load|Supersampl'"
    echo "sleep 1; sync"
    echo "echo o > /proc/sysrq-trigger"
    echo "sleep 3"
  } >> "$init"
  chmod +x "$init"

  echo "--- $name"
  timeout 600 "$UML/run.sh" "/pass-$name-init.sh" 2>&1 \
    | grep -vE "setup_one_line|ubd device|tempdir|^\s*$" > "$OUT/$name.txt"
  # The interesting lines are whatever the probe printed; the boot chatter is
  # not. Show the verdicts rather than the whole log.
  grep -vE "^(spidev|capture|crw|=== |Python 3)" "$OUT/$name.txt" \
    | grep -vE "^\s*$" | tail -14 | sed 's/^/    /' 

  if [ "$capture" = yes ] && [ -s "$UML/out/$name.bin" ]; then
    mkdir -p "$OUT/$name"
    python3 "$RIG/decode_st7789.py" "$UML/out/$name.bin" \
      "$OUT/$name/s.png" --split | tail -1
    mv "$UML/out/$name.bin" "$OUT/$name.bin"
  fi
  echo
}

run_one silent-pay  musig2_silent_payment_probe.py  no
run_one one-visit   card_one_visit_probe.py  no
run_one rounds      card_rounds_probe.py     no
run_one offer       shoot_card_offer.py      yes
run_one refusal     shoot_musig2_refusal.py  yes
# The happy path, captured rather than only asserted: a spend the coordinator
# armed with a nonce this card published earlier, which signs in one visit. Its
# screen is the one that used to be wrong, so it is worth a picture.
run_one one-visit-screen shoot_one_visit.py yes
run_one flow-musig2 "flow_walk.py musig2"    yes
run_one flow-normal "flow_walk.py normal"    yes

# --- the applet layer -------------------------------------------------------
# These need no kernel devices, only python and a JVM, so they run here rather
# than inside the sandbox.
# Whichever python has embit and pysatochip. Defaulting to plain `python3` made
# this layer depend on the caller having a venv activated, which is invisible: a
# pass run from a fresh shell skipped the applet layer with an import error while
# one run from an activated shell passed, from identical code.
APPLET_PY="${APPLET_PY:-$UML/pyvenv/bin/python3}"
[ -x "$APPLET_PY" ] || APPLET_PY="$(command -v python3)"

applet_ran=no
if [ -n "${JCARDSIM_JAR:-}" ] && [ -n "${APPLET_SRC:-}" ] && [ -n "${JAVA_HOME:-}" ]; then
  if [ ! -d "$APPLET_SRC/test/classes" ]; then
    echo "--- applet layer: SKIPPED, $APPLET_SRC/test/classes missing"
    echo "    compile AppletPipe first: $APPLET_SRC/test/run.sh"
  elif ! "$APPLET_PY" -c "import embit, pysatochip" 2>/dev/null; then
    echo "--- applet layer: SKIPPED, $APPLET_PY has no embit/pysatochip"
    echo "    set APPLET_PY to a python that does"
  else
    applet_ran=yes
    export PYTHONPATH="$RIG/jcardsim:$APP_SRC:$(dirname "$APP_SRC")/tests"
    echo "    python: $APPLET_PY"
    for probe in contract_test demo1_nonce_vault demo2_silent_payment; do
      echo "--- applet: $probe"
      timeout 600 "$APPLET_PY" "$RIG/jcardsim/$probe.py" 2>&1 \
        | grep -vE "RuntimeWarning|shadowing|remove this package|from smartcard" \
        > "$OUT/applet-$probe.txt"
      sed 's/^/    /' "$OUT/applet-$probe.txt" | tail -10
      echo
    done
  fi
else
  echo "--- applet layer: SKIPPED, set JAVA_HOME, JCARDSIM_JAR and APPLET_SRC"
  echo "    the real card is not covered by this pass"
fi

echo "screens:  $(find "$OUT" -name 's-*.png' | wc -l)"
# "ran" is all this can honestly claim: it does not read the probes' verdicts.
# release_gate.sh does, and refuses a pass where one of them said nothing.
echo "display layer: 8 probe(s) ran (verdicts not checked here -- see release_gate.sh)"
echo "applet layer:  $applet_ran"
[ "$applet_ran" = yes ] || echo "PARTIAL PASS: the applet was not exercised"
echo "everything in $OUT"
{ echo "display layer: 8 probe(s) ran, verdicts not checked"
  echo "applet layer: $applet_ran"; } >> "$OUT/provenance.txt"
