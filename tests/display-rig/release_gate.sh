#!/usr/bin/env bash
# The check to run before tagging a release. It does not tag or publish anything.
#
#   ./release_gate.sh [app-src] [ref-being-released]
#
# run_all.sh answers "did a pass happen". That is not the same question as "is
# this good enough to ship", and it cannot be: it prints "display layer: ran"
# whether the probes proved anything or timed out, and it exits 0 either way.
# This reads the pass it produced and requires each probe to have said the thing
# it exists to say.
#
# What it will not let past:
#
#   an app tree with uncommitted changes, because the release would not be
#     reproducible from the commit it claims to be
#   a partial pass, where the applet layer was skipped for want of a JVM
#   a probe that produced no verdict, crashed, or left a Traceback
#   a capture that decoded to no screens
#
# What it cannot do is look at the screens. A screen can render perfectly and
# still say the wrong thing, which has happened here, and no assertion catches
# that. So the gate ends by naming the screens a human still has to open.
set -uo pipefail

RIG="$(cd "$(dirname "$0")" && pwd)"
APP_SRC="${1:-/home/rob/apps/doomsigner/src}"
WANT_REF="${2:-}"
UML="${UML_ROOT:-/home/rob/apps/_scratch/uml}"

# The applet layer is not optional here, unlike in run_all.sh, so its inputs get
# defaults rather than a shrug.
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-8-openjdk-amd64}"
export JCARDSIM_JAR="${JCARDSIM_JAR:-/home/rob/apps/_scratch/jctools/jcardsim/target/jcardsim-3.0.5-SNAPSHOT.jar}"
export APPLET_SRC="${APPLET_SRC:-/home/rob/apps/_scratch/applet-src/SeedKeeper-Applet}"

problems=()
checks=()
note() { echo "  $1"; }
bad()  { echo "  REFUSED  $1"; problems+=("$1"); }
# Recorded as well as printed: GATE.txt is the evidence that travels with the
# release, and a verdict with no list of what was checked is not evidence.
pass() { echo "  ok   $1"; checks+=("ok   $1"); }

echo "release gate"
echo "============"
echo

# ---- 1. is the thing being released a thing that exists -------------------

REPO_DIR="$(cd "$APP_SRC/.." && pwd)"
HEAD_SHA="$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null || echo '')"
HEAD_REF="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
DIRTY="$(git -C "$REPO_DIR" status --porcelain 2>/dev/null | wc -l)"
DESCRIBE="$(git -C "$REPO_DIR" describe --tags --always --dirty 2>/dev/null || echo '?')"

echo "the app"
note "source    : $APP_SRC"
note "ref       : $HEAD_REF  ($DESCRIBE)"
note "commit    : ${HEAD_SHA:0:12}"
[ -n "$HEAD_SHA" ] || bad "$REPO_DIR is not a git tree, so nothing can be pinned to it"
[ "$DIRTY" = "0" ] || bad "$DIRTY uncommitted file(s): this release would not rebuild from its commit"
if [ -n "$WANT_REF" ]; then
  WANT_SHA="$(git -C "$REPO_DIR" rev-parse "$WANT_REF^{commit}" 2>/dev/null || echo '')"
  if [ -z "$WANT_SHA" ]; then
    bad "no such ref in the app tree: $WANT_REF"
  elif [ "$WANT_SHA" != "$HEAD_SHA" ]; then
    bad "the tree is at ${HEAD_SHA:0:12} but $WANT_REF is ${WANT_SHA:0:12}"
  else
    note "releasing : $WANT_REF, and the tree is on it"
  fi
else
  note "releasing : whatever is checked out (pass a ref to pin it)"
fi

echo
echo "the applet"
if [ -d "$APPLET_SRC/.git" ]; then
  note "source    : $APPLET_SRC"
  note "commit    : $(git -C "$APPLET_SRC" log --oneline -1 2>/dev/null)"
  A_DIRTY="$(git -C "$APPLET_SRC" status --porcelain 2>/dev/null | wc -l)"
  [ "$A_DIRTY" = "0" ] || bad "the applet tree has $A_DIRTY uncommitted file(s)"
else
  bad "no applet source at $APPLET_SRC"
fi
[ -f "$JCARDSIM_JAR" ] || bad "no jcardsim jar at $JCARDSIM_JAR"
[ -d "$APPLET_SRC/test/classes" ] || bad "$APPLET_SRC/test/classes missing: compile AppletPipe first"

if [ ${#problems[@]} -ne 0 ]; then
  echo
  echo "GATE: REFUSED before running anything, ${#problems[@]} problem(s) above."
  exit 1
fi

# ---- 2. the checks that need no sandbox -----------------------------------

echo
echo "the decoder"
if APP_SRC="$APP_SRC" python3 "$RIG/test_decode_st7789.py" > /tmp/gate-decode.txt 2>&1 \
   && APP_SRC="$APP_SRC" python3 "$RIG/test_decode_mutations.py" > /tmp/gate-mutate.txt 2>&1; then
  note "$(tail -1 /tmp/gate-decode.txt)"
  note "$(tail -1 /tmp/gate-mutate.txt)"
else
  bad "the decoder checks failed; see /tmp/gate-decode.txt and /tmp/gate-mutate.txt"
fi

# ---- 3. the full pass ------------------------------------------------------

echo
echo "the pass (this takes a few minutes)"
"$RIG/run_all.sh" "$APP_SRC" > /tmp/gate-pass.txt 2>&1
OUT="$(grep -oE "$UML/out/pass-[0-9-]+" /tmp/gate-pass.txt | tail -1)"
if [ -z "$OUT" ] || [ ! -d "$OUT" ]; then
  bad "run_all.sh produced no pass directory; see /tmp/gate-pass.txt"
  echo
  echo "GATE: REFUSED, ${#problems[@]} problem(s) above."
  exit 1
fi
note "results   : $OUT"
cp /tmp/gate-pass.txt "$OUT/run_all.log"

# Each probe and the line it must print. Deliberately the probe's own words: if
# someone changes what a probe claims to have proven, this fails and a human
# reads the new wording, which is the right outcome. Silence is the failure mode
# worth being noisy about.
check_said() {  # file, description, pattern
  if grep -qE -- "$3" "$OUT/$1.txt" 2>/dev/null; then
    pass "$2"
  else
    bad "$1 never said: $2"
  fi
}

echo
echo "the display layer"
check_said silent-pay  "the silent payment path completed" \
           "VERDICT: the silent payment path completed"
check_said one-visit   "one visit, and the pooled nonce was the one used" \
           "VERDICT: one visit, and the pooled nonce was the one used"
check_said rounds      "the card refused a replayed seal" \
           "safe: the card refused"
check_said offer       "the card offer leads to the round screen" \
           "Keep Device On -> PSBTMusig2RoundView"
check_said offer       "an open card skips the offer entirely" \
           "card already open, no offer -> PSBTMusig2RoundView"
check_said refusal     "a refused card returns to the main menu" \
           "refusal led to: MainMenuView"
check_said one-visit-screen "a coordinator-armed spend signs in one visit" \
           "VERDICT: one visit, signed, and the screen counts no steps"
check_said flow-musig2 "the musig2 flow reaches the signed QR" \
           "FLOW:.*PSBTMusig2RoundView.*PSBTSignedQRDisplayView"
check_said flow-normal "an ordinary spend reaches the signed QR first time" \
           "FLOW:.*PSBTOverviewView.*PSBTSignedQRDisplayView"

echo
echo "the applet layer"
if ! grep -q "^applet layer: yes" "$OUT/provenance.txt" 2>/dev/null; then
  bad "the applet layer did not run: this is a partial pass, not a release"
else
  check_said applet-contract_test "the fake still answers like the applet" \
             "PASS: the fake answers like the applet"
  check_said applet-demo1_nonce_vault "the applet refused a replayed nonce" \
             "replay refused by the applet"
  check_said applet-demo2_silent_payment "a silent payment signed from two real applets" \
             "VERDICT: silent payment signed, both nonces from real applets"
fi

echo
echo "the screens"
SCREENS=0
for capture in offer refusal one-visit-screen flow-musig2 flow-normal; do
  count=$(find "$OUT/$capture" -name 's-*.png' 2>/dev/null | wc -l)
  SCREENS=$((SCREENS + count))
  if [ "$count" -eq 0 ]; then
    bad "$capture decoded to no screens: the capture is empty or the decode failed"
  else
    pass "$capture: $count screen(s)"
  fi
done

echo
echo "crashes"
CRASHED="$(grep -lE "^Traceback|Segmentation fault|Kernel panic" "$OUT"/*.txt 2>/dev/null \
           | xargs -r -n1 basename | tr '\n' ' ')"
if [ -n "$CRASHED" ]; then
  bad "something crashed in: $CRASHED"
else
  pass "nothing crashed"
fi

# ---- 4. the verdict --------------------------------------------------------

{
  echo "release gate, $(date -u +%Y-%m-%dT%H:%MZ)"
  echo
  cat "$OUT/provenance.txt"
  echo "app describe: $DESCRIBE"
  echo "ref asked for: ${WANT_REF:-none}"
  echo "screens: $SCREENS"
  echo
  [ ${#checks[@]} -eq 0 ] || printf '  %s\n' "${checks[@]}"
  echo
  if [ ${#problems[@]} -eq 0 ]; then
    echo "MACHINE VERDICT: every automated check passed."
    echo "STILL A HUMAN STEP: nobody has looked at the screens."
  else
    echo "MACHINE VERDICT: REFUSED"
    printf '  %s\n' "${problems[@]}"
  fi
} > "$OUT/GATE.txt"

echo
if [ ${#problems[@]} -ne 0 ]; then
  echo "GATE: REFUSED, ${#problems[@]} problem(s):"
  printf '  - %s\n' "${problems[@]}"
  echo "written to $OUT/GATE.txt"
  exit 1
fi

cat <<EOM
GATE: the automated half passed. $SCREENS screen(s) captured.

Not done yet. A screen can render perfectly and still say the wrong thing, and
no check here can tell. Open these before tagging:

  $OUT/flow-musig2/     the whole musig2 round, in order
  $OUT/offer/           the card offer screen and its wording
  $OUT/refusal/         what a refused card looks like

Then tag. This script does not tag, publish or upload anything.
Written to $OUT/GATE.txt
EOM
