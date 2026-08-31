#!/bin/bash
# =============================================================================
# test_evidence_provenance.sh — negative test for evidence provenance and
# checkpoint pickle safety (plan todo 6, RED before todo 11 fix)
# =============================================================================
# Proves three defects in the CURRENT evidence/checkpoint machinery:
#
#   (a) Stale-evidence reuse (run_ibex_segment_run.sh:70-77): when the wall-time
#       cap fires (RUN_RC=124), the runner APPENDS two fixed strings to a
#       FIXED-PATH evidence file (build/evidence/task-14-...-signoff.txt) and
#       exits 0.  A pre-existing file from an OLD run (with its own
#       `status=PASS` lines and old commit) is therefore treated as this run's
#       result: the old PASS survives, the new run's entries are appended
#       indistinguishably, and nothing is keyed to THIS run.
#   (b) Missing provenance fields: the evidence writer
#       (sim/rtl_soc_segment_run.py _write_evidence) emits Commit/Command/Dims/
#       per-layer status, but NO run ID, NO simv hash, NO dirty-state record,
#       NO RTL flist / python driver / firmware / golden hash, NO tool
#       versions.  The real evidence sample on disk even carries a commit
#       (c506f8ec...) that does NOT match the current HEAD.
#   (c) Unsafe pickle loading (sim/rtl_soc_segment_run.py:346,412): the
#       checkpoint NPZ and spike NPZ are loaded with np.load(..., allow_pickle
#       =True) with no content validation and no commit check — an untrusted
#       local file executes arbitrary code on load.
#
# RED/GREEN contract (design rule for this TDD test):
#   * Today (before W2 todo 11 adds provenance + safe resume) this script MUST
#     exit non-zero (RED) and print FAIL [RED] per failing assertion.
#   * After todo 9 (fresh run-keyed evidence files) and todo 11 (hash binding
#     + safe checkpoint resume) land, the SAME script MUST exit 0 (GREEN).
#     It achieves this by extracting the runner's timeout block VERBATIM from
#     the live file at run time and by grepping the live code for the fields
#     todo 11 must emit, so the test tracks whatever the fix changes.
#
# Why verbatim extraction instead of invoking the full runner:
#   * run_ibex_segment_run.sh sources run_env.sh, which requires the EDA server
#     (/NAS/Tools/EDA/env/modules.bash) and aborts at that gate on any non-EDA
#     host.  Extracting the timeout decision block verbatim probes exactly the
#     append logic todo 9/11 will edit, with no EDA dependency and no risk of
#     writing to the REAL build/evidence tree (REPO_ROOT is redirected into a
#     private mktemp sandbox).
#   * sim/rtl_soc_segment_run.py imports cocotb/VCS-only modules, so the pickle
#     assertions probe its SOURCE (grep for the exact load calls) and
#     demonstrate the runtime behavior with numpy alone in a sandbox.
#
# Extraction contract with the W2 fixer (todos 9 + 11):
#   * Assertion (a) anchors on a line containing:   if [ "$RUN_RC" -eq 124 ]
#     and extracts that line through EOF.  The fix must, inside this region:
#       - never append to a PRE-EXISTING evidence file (a1: old file's bytes
#         unchanged after the run),
#       - write evidence to a run-keyed path (a2: a fresh file distinct from
#         the pre-existing one must appear; a2b: the EVIDENCE= assignment must
#         contain a run-id/timestamp variable such as $RUN_ID),
#       - make two consecutive timeout runs distinguishable (a3: >=2 files or
#         non-duplicate per-run entries).
#   * Assertion (b) greps the code path — the runner, the segment script, and
#     scripts/gen_evidence_provenance.py if it exists — for the plan's 8
#     provenance field groups.  Todo 11 must make these greps hit (emitting
#     provenance for the evidence-writing path):
#       dirty state  : 'porcelain|dirty'
#       run ID       : 'run[-_]?id'
#       simv hash    : 'simv' AND 'sha256'
#       flist hash   : 'flist.*sha256|sha256.*flist'
#       driver hash  : 'driver.*sha256|sha256.*driver'
#       firmware hash: 'firmware.*sha256|sha256.*firmware'
#       golden/ckpt  : '(golden|checkpoint).*sha256|sha256.*(golden|checkpoint)'
#       tool versions: 'vcs -ID|-ID|tool[_ ]version'
#       timestamp    : 'Timestamp start|timestamp'  (already emitted today)
#   * Assertion (c) contracts on sim/rtl_soc_segment_run.py:
#       - no `allow_pickle=True` may remain in the NPZ load paths,
#       - the `_resume_from_npz` function body must guard the load against a
#         corrupted NPZ (try/except) and must validate the checkpoint's
#         metadata commit against the current git HEAD.
#
# Hazards addressed:
#   * The REAL evidence file and REAL checkpoints under build/evidence are
#     NEVER touched.  All extracted-code execution runs with REPO_ROOT and
#     cwd redirected into a private mktemp sandbox removed by an EXIT trap.
#   * The real simv is never invoked; no VCS/cocotb/EDA dependency exists.
#
# Assertion style (anti-misleading-success):
#   * This test asserts file-content deltas, exit codes, and grep hits against
#     the LIVE scripts.  It never greps command output for fabricated text and
#     never fabricates the RED state: every FAIL below reproduces the actual
#     current behavior of the real files.
#
# Exit codes of THIS test script:
#   0  GREEN — all assertions passed (expected only after todo 9/11 fixes)
#   1  RED   — at least one assertion failed (expected today)
#   3  INVALID ENVIRONMENT — prerequisites missing
# =============================================================================

set -u          # no `set -e`: assertions drive control flow explicitly

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# RUNNER_UNDER_TEST / SEGMENT_PY_UNDER_TEST overrides let the W2 fixer
# dry-verify GREEN against fixed copies without editing product scripts.
RUNNER="${RUNNER_UNDER_TEST:-$REPO_ROOT/sim/regression/run_ibex_segment_run.sh}"
RUNNER_NAME="$(basename "$RUNNER")"
SEGMENT_PY="${SEGMENT_PY_UNDER_TEST:-$REPO_ROOT/sim/rtl_soc_segment_run.py}"
SEGMENT_PY_NAME="$(basename "$SEGMENT_PY")"
EVIDENCE_SAMPLE="${EVIDENCE_SAMPLE:-$REPO_ROOT/build/evidence/task-14-soc-rtl-verification-signoff.txt}"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/test_evidence_provenance.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

PASSES=0
FAILURES=0
pass() { PASSES=$((PASSES + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAILURES=$((FAILURES + 1)); printf 'FAIL [RED]: %s\n' "$1"; }

echo "=== test_evidence_provenance.sh (todo 6 negative test) ==="
echo "REPO_ROOT:  $REPO_ROOT"
echo "RUNNER:     $RUNNER"
echo "SEGMENT_PY: $SEGMENT_PY"
echo "HEAD:       $(git -C "$REPO_ROOT" rev-parse HEAD) ($(git -C "$REPO_ROOT" branch --show-current))"
echo "TMP sandbox: $TMP_DIR"
if [ -f "$RUNNER" ]; then
    echo "RUNNER SHA256:     $(sha256sum "$RUNNER" | awk '{print $1}')"
else
    echo "ERROR: runner not found: $RUNNER"
    exit 3
fi
if [ -f "$SEGMENT_PY" ]; then
    echo "SEGMENT_PY SHA256: $(sha256sum "$SEGMENT_PY" | awk '{print $1}')"
else
    echo "ERROR: segment script not found: $SEGMENT_PY"
    exit 3
fi

# ── 0. Environment sanity ─────────────────────────────────────────────────
for dep in bash sed grep sha256sum mktemp sort uniq git python3; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        echo "ERROR: prerequisite '$dep' not found on PATH"
        exit 3
    fi
done
if ! python3 -c "import numpy" 2>/dev/null; then
    echo "ERROR: python3 numpy not importable (needed for the pickle-safety behavioral probe)"
    exit 3
fi
pass "environment sanity: all prerequisites present (bash/sed/grep/sha256sum/git/python3+numpy)"

mkdir -p "$TMP_DIR/build/evidence"

# ═════════════════════════════════════════════════════════════════════════
# (a) Stale-evidence reuse: old PASS must not satisfy a new run
# ═════════════════════════════════════════════════════════════════════════
ANCHOR_A="$(grep -nF 'if [ "$RUN_RC" -eq 124 ]' "$RUNNER" | head -1 | cut -d: -f1)"
echo
echo "--- assertion (a): stale-evidence reuse (old PASS must not satisfy new run) ---"
echo "extraction anchor (runner line with the 124 decision): ${ANCHOR_A:-<NOT FOUND>}"
if [ -z "$ANCHOR_A" ]; then
    fail "extraction contract broken: no 'if [ \"\$RUN_RC\" -eq 124 ]' line in $RUNNER_NAME (todo 9 must keep this decision anchor)"
else
    sed -n "${ANCHOR_A},\$p" "$RUNNER" > "$TMP_DIR/decision_block.sh"
    echo "[verbatim extracted timeout decision block from $RUNNER_NAME:$ANCHOR_A..EOF]"
    cat "$TMP_DIR/decision_block.sh"
    echo "[end extracted block]"

    # Pre-place OLD evidence from a *previous* run at the runner's fixed path.
    # Faithful copy of the real evidence format (header + old commit + old
    # status=PASS lines).  This simulates "stale evidence exists".
    OLD_EVIDENCE="$TMP_DIR/build/evidence/task-14-soc-rtl-verification-signoff.txt"
    cat > "$OLD_EVIDENCE" <<EOF
Task 14 - SoC RTL Verification Signoff: Ibex 36-layer 8-checkpoint segment run
======================================================================
Timestamp start : 2026-01-01T00:00:00Z
Commit          : deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
Command         : bash sim/regression/run_ibex_segment_run.sh
Driver host     : sz0001 (Ibex SoC VCS + firmware)
engine=ibex
checkpoints=L0,L5,L10,L15,L20,L25,L30,L35
chain_restart=true
commands_dispatched=510
elapsed_s=47241.5
layer=0 engine=ibex cos_sim=1.000000 threshold=0.999 status=PASS chain_restart_state_source=ibex_dram
layer=5 engine=ibex cos_sim=1.000000 threshold=0.999 status=PASS chain_restart_state_source=ibex_dram
  checkpoints_passed=8/8
  LADDER=PASS
  Overall: PASS
  Timestamp end: 2026-01-01T12:00:00Z
EOF
    OLD_SHA_BEFORE="$(sha256sum "$OLD_EVIDENCE" | awk '{print $1}')"
    echo "[pre-placed OLD evidence at $OLD_EVIDENCE (sha256=$OLD_SHA_BEFORE) — contains literal 'status=PASS' lines from an old run]"

    # Feed the verbatim extracted append path a simulated timeout (RUN_RC=124),
    # with REPO_ROOT redirected into the sandbox so nothing real is written.
    RUN_RC=124 SEG_TIMEOUT_S=86400 REPO_ROOT="$TMP_DIR" \
        bash "$TMP_DIR/decision_block.sh" > "$TMP_DIR/a.out" 2>&1
    RC_A=$?
    echo "[real output of the extracted block with RUN_RC=124 (misleading-success-output evidence):]"
    cat "$TMP_DIR/a.out" | sed 's/^/    | /'
    echo "[extracted block exit code with RUN_RC=124: $RC_A (0 == 'timed-out run reports SUCCESS'; exit-code mapping is asserted by test_timeout_behavior.sh todo 1)]"

    # (a1) The pre-existing old evidence must NOT be modified by the new run —
    #      fresh evidence must be written instead.
    OLD_SHA_AFTER="$(sha256sum "$OLD_EVIDENCE" | awk '{print $1}')"
    echo "old evidence sha256 before: $OLD_SHA_BEFORE"
    echo "old evidence sha256 after : $OLD_SHA_AFTER"
    if [ "$OLD_SHA_BEFORE" = "$OLD_SHA_AFTER" ]; then
        pass "a1: pre-existing old evidence left UNMODIFIED by the new run (sha256 unchanged)"
    else
        fail "a1: pre-existing old evidence was APPENDED TO by the new run (sha256 changed) — the runner reuses/extends stale evidence instead of writing fresh evidence keyed to this run"
    fi

    # (a2) A fresh evidence file keyed to THIS run must exist after the run.
    FRESH_COUNT=$(find "$TMP_DIR/build/evidence" -maxdepth 1 -name '*.txt' ! -name "$(basename "$OLD_EVIDENCE")" | wc -l)
    echo "fresh evidence files created (excl. pre-existing old file): $FRESH_COUNT"
    if [ "$FRESH_COUNT" -ge 1 ]; then
        pass "a2: fresh run-keyed evidence file(s) written by the new run"
    else
        fail "a2: no fresh evidence file written — the new run's record lives only inside the PRE-EXISTING old evidence file (stale PASS satisfies the new run)"
    fi

    # (a2b) The append target path must itself be run-keyed (contain a run-id
    #       / timestamp variable), not a fixed literal path shared with old runs.
    if grep -qE 'EVIDENCE=.*(\$RUN_ID|\$RUNID|\$EPOCH|[Tt]imestamp|date \+)' "$TMP_DIR/decision_block.sh"; then
        pass "a2b: evidence path is run-keyed (run-id/timestamp variable in the EVIDENCE assignment)"
    else
        fail "a2b: evidence path is a FIXED literal ($(grep -oE 'EVIDENCE="[^"]*"' "$TMP_DIR/decision_block.sh" | head -1)) shared by every run — a new run cannot be told apart from an old one by its evidence path"
    fi

    # (a3) PENDING/timebox entries of two consecutive runs must be
    #      distinguishable (scenario: PENDING evidence).  Run the block a
    #      SECOND time and check for duplicated indistinguishable entries.
    RUN_RC=124 SEG_TIMEOUT_S=86400 REPO_ROOT="$TMP_DIR" \
        bash "$TMP_DIR/decision_block.sh" > "$TMP_DIR/a3.out" 2>&1
    echo "[second simulated timeout executed — checking run distinguishability]"
    FILES_AFTER=$(find "$TMP_DIR/build/evidence" -maxdepth 1 -name '*.txt' | wc -l)
    DUPES=$(grep -c 'timebox_status=TIMEOUT_24H' "$OLD_EVIDENCE" || true)
    echo "evidence files after 2 runs: $FILES_AFTER; timebox_status lines in old file: $DUPES"
    echo "[current evidence tail after 2 runs:]"
    tail -n 5 "$OLD_EVIDENCE" | sed 's/^/    | /'
    if [ "$FILES_AFTER" -ge 2 ] || { [ "$FILES_AFTER" -eq 1 ] && [ "$DUPES" -le 1 ]; }; then
        pass "a3: two consecutive timeout runs produce distinguishable evidence"
    else
        fail "a3: two consecutive timeout runs produced $DUPES IDENTICAL timebox entries in the SAME file — a prior run's PENDING/TIMEOUT entry is indistinguishable from this run's (no per-run binding)"
    fi
fi

# ═════════════════════════════════════════════════════════════════════════
# (b) Provenance fields: evidence must bind commit/run-id/hashes/toolchain
# ═════════════════════════════════════════════════════════════════════════
echo
echo "--- assertion (b): provenance fields ---"
# Code path todo 11 will edit: runner + segment script + the provenance
# generator script it must create.
CODE_FILES="$RUNNER $SEGMENT_PY"
if [ -f "$REPO_ROOT/scripts/gen_evidence_provenance.py" ]; then
    CODE_FILES="$CODE_FILES $REPO_ROOT/scripts/gen_evidence_provenance.py"
fi
echo "code path probed for provenance emission: $CODE_FILES"

check_field() {  # $1 = label, $2 = grep -E pattern
    local label="$1" pattern="$2"
    if grep -qiE "$pattern" $CODE_FILES; then
        pass "b: code path emits $label (pattern: $pattern)"
    else
        fail "b: code path emits NO $label — evidence is not bound to $label (pattern '$pattern' absent from $RUNNER_NAME / $SEGMENT_PY_NAME)"
    fi
}

check_field "git dirty state (status --porcelain)"        'porcelain|dirty'
check_field "run ID"                                      'run[-_]?id'
if grep -qiE 'simv' $CODE_FILES && grep -qiE 'sha256' $CODE_FILES; then
    pass "b: code path emits simv hash (both 'simv' and 'sha256' present)"
else
    fail "b: code path emits NO simv hash (need both 'simv' and 'sha256') — evidence is not bound to the simv binary"
fi
check_field "RTL flist hash"                              'flist.*sha256|sha256.*flist'
check_field "python driver hash"                          'driver.*sha256|sha256.*driver'
check_field "firmware ELF/HEX hash"                       'firmware.*sha256|sha256.*firmware'
check_field "golden/checkpoint hash"                      '(golden|checkpoint).*sha256|sha256.*(golden|checkpoint)'
check_field "tool versions (vcs -ID etc.)"                'vcs -ID|-ID|tool[_ ]version'
check_field "timestamp"                                   'Timestamp start|timestamp'

# (b0) Real evidence sample: its Commit line must equal the CURRENT git HEAD,
#      and the sample must carry the provenance keys.
echo
echo "evidence sample: ${EVIDENCE_SAMPLE} $([ -f "$EVIDENCE_SAMPLE" ] && echo '(exists)' || echo '(MISSING)')"
if [ -f "$EVIDENCE_SAMPLE" ]; then
    SAMPLE_COMMIT="$(awk '/^Commit/{print $NF}' "$EVIDENCE_SAMPLE" | head -1)"
    HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "sample Commit line : ${SAMPLE_COMMIT:-<none>}"
    echo "current git HEAD   : $HEAD"
    if [ -n "$SAMPLE_COMMIT" ] && [ "$SAMPLE_COMMIT" = "$HEAD" ]; then
        pass "b0: evidence sample commit matches current git HEAD"
    else
        fail "b0: evidence sample commit (${SAMPLE_COMMIT:-absent}) does NOT match current git HEAD ($HEAD) — existing evidence is not bound to the checked-out tree"
    fi
    MISSING_KEYS=""
    for KEY in run_id simv_sha256 flist_sha256 driver_sha256 firmware_sha256 golden_sha256 checkpoint_sha256 tool_versions dirty; do
        if ! grep -qiE "$KEY" "$EVIDENCE_SAMPLE"; then
            MISSING_KEYS="$MISSING_KEYS $KEY"
        fi
    done
    if [ -z "$MISSING_KEYS" ]; then
        pass "b: evidence sample carries all required provenance keys"
    else
        fail "b: evidence sample is MISSING required provenance keys:$MISSING_KEYS"
    fi
    echo "[sample head for reference:]"
    sed -n '1,8p' "$EVIDENCE_SAMPLE" | sed 's/^/    | /'
else
    echo "[INFO] no real evidence sample present — b0/sample-key checks skipped (code-path checks above still bind todo 11)"
fi

# ═════════════════════════════════════════════════════════════════════════
# (c) Pickle safety: checkpoint/spike NPZ must not allow_pickle=True-load
#     untrusted local files; corrupted NPZ rejected; commit validated.
# ═════════════════════════════════════════════════════════════════════════
echo
echo "--- assertion (c): pickle safety + checkpoint resume validation ---"
echo "[real NPZ load sites in $SEGMENT_PY_NAME:]"
grep -nE 'allow_pickle|NPZ_PATH =|SPIKE_NPZ =' "$SEGMENT_PY" | sed 's/^/    | /'

# (c1) Static: no allow_pickle=True in the load paths.
ALLOW_TRUE=$(grep -c 'allow_pickle=True' "$SEGMENT_PY" || true)
if [ "$ALLOW_TRUE" -eq 0 ]; then
    pass "c1: no allow_pickle=True in $SEGMENT_PY_NAME (checkpoint/spike loads are pickle-safe)"
else
    fail "c1: $SEGMENT_PY_NAME contains $ALLOW_TRUE occurrence(s) of allow_pickle=True — untrusted local NPZ files can execute arbitrary code on load"
fi

# (c1b) Behavioral proof: with numpy alone, demonstrate that the EXACT load
#       call the real code makes executes a pickle payload.
echo "[behavioral proof — pickle payload execution under the real load call:]"
python3 - "$TMP_DIR" <<'EOF'
import os, sys, pathlib
import numpy as np
d = pathlib.Path(sys.argv[1])
canary = d / "PICKLE_EXECUTED"
class Bomb:
    def __reduce__(self):
        return (os.system, (f"touch {canary}",))
np.savez(d / "bomb.npz", arr=np.array([Bomb()], dtype=object))
with np.load(d / "bomb.npz", allow_pickle=True) as z:   # exact call used by the real script (:346,:412)
    _ = z["arr"]
print("    | allow_pickle=True load+access -> canary file created:", canary.exists(), "(arbitrary code executed)")
canary.unlink(missing_ok=True)
try:
    with np.load(d / "bomb.npz", allow_pickle=False) as z:
        _ = z["arr"]
    print("    | allow_pickle=False: unexpectedly loaded")
except ValueError as e:
    print("    | allow_pickle=False rejects object arrays ->", str(e)[:60], "(safe alternative exists)")
EOF

# (c2) Corrupted NPZ must be rejected gracefully: the _resume_from_npz body
#      must guard the load.  Extract the function body verbatim and check.
echo
echo "[verbatim _resume_from_npz body from $SEGMENT_PY_NAME:]"
sed -n '/^def _resume_from_npz/,/^def /p' "$SEGMENT_PY" | sed '$d' > "$TMP_DIR/resume_body.py"
cat "$TMP_DIR/resume_body.py" | sed 's/^/    | /'
# The guard must wrap the LOAD itself: a try/except that appears only AFTER
# np.load (e.g. around the evidence-file read, as today) does not protect the
# np.load call from a corrupted checkpoint NPZ.
LOAD_LINE=$(grep -nE 'np\.load' "$TMP_DIR/resume_body.py" | head -1 | cut -d: -f1)
TRY_LINE=$(grep -nE 'try:' "$TMP_DIR/resume_body.py" | head -1 | cut -d: -f1)
echo "first np.load line in body: ${LOAD_LINE:-<none>}; first try: line: ${TRY_LINE:-<none>}"
if [ -n "$LOAD_LINE" ] && [ -n "$TRY_LINE" ] && [ "$TRY_LINE" -lt "$LOAD_LINE" ]; then
    pass "c2: _resume_from_npz guards the checkpoint LOAD itself (try/except preceding np.load) — corrupted NPZ rejected gracefully"
else
    fail "c2: _resume_from_npz does NOT guard the np.load call (first try: at line ${TRY_LINE:-none}, first np.load at line ${LOAD_LINE:-none}) — a corrupted checkpoint NPZ crashes the run with an unhandled BadZipFile traceback instead of being rejected gracefully"
fi
echo "[behavioral proof — corrupted NPZ under the real load call:]"
python3 - "$TMP_DIR" <<'EOF'
import pathlib, sys
import numpy as np
d = pathlib.Path(sys.argv[1])
(d / "corrupt.npz").write_bytes(b"PK\x03\x04garbage-truncated")
try:
    with np.load(d / "corrupt.npz", allow_pickle=True) as z:   # exact call used by the real script
        print("    | corrupt npz loaded:", z.files)
except Exception as e:
    print("    | corrupt npz raises unhandled:", type(e).__name__, "-", str(e)[:60])
EOF

# (c3) Wrong-commit scenario: the resume path must validate the checkpoint
#      npz metadata commit against the current git HEAD before restoring.
echo
if grep -qiE 'commit|metadata' "$TMP_DIR/resume_body.py"; then
    pass "c3: _resume_from_npz validates checkpoint metadata/commit before restoring state"
else
    fail "c3: _resume_from_npz never reads the checkpoint's metadata commit — a checkpoint npz generated at a DIFFERENT commit (or planted by hand) is restored blindly with no provenance check"
fi
echo "[behavioral proof — wrong-commit checkpoint accepted by the current load path:]"
python3 - "$TMP_DIR" <<'EOF'
import json, pathlib, sys
import numpy as np
d = pathlib.Path(sys.argv[1])
data = {"layer_0_output": np.zeros((1, 2048), np.float32),
        "hw_layer_0_output": np.zeros((1, 2048), np.int32),
        "metadata": np.array([json.dumps({"commit": "0000000000000000000000000000000000000000",
                                          "engine": "ibex", "partial": True})])}
np.savez(d / "wrong_commit.npz", **data)
with np.load(d / "wrong_commit.npz", allow_pickle=True) as z:   # exact call used by the real script
    print("    | wrong-commit checkpoint loaded without rejection; files =", z.files)
EOF

# ── Informational: full-runner smoke (no assertion) ───────────────────────
echo
echo "--- informational: full-runner smoke (no assertion) ---"
if [ -f /NAS/Tools/EDA/env/modules.bash ]; then
    echo "[INFO] EDA mount present; skipping full-runner smoke to avoid arming the runner's pkill EXIT trap on a shared host"
else
    SEG_TIMEOUT_S=1 bash "$RUNNER" > "$TMP_DIR/smoke.out" 2>&1
    SMOKE_RC=$?
    echo "[INFO] SEG_TIMEOUT_S=1 bash $RUNNER_NAME -> exit $SMOKE_RC"
    echo "[INFO] (on non-EDA hosts run_env.sh aborts the script at its EDA gate before the evidence region, hence the verbatim-extraction assertions above)"
    sed -n '1,3p' "$TMP_DIR/smoke.out" | sed 's/^/[INFO] smoke output: /'
fi

# ── Four negative scenarios coverage note ─────────────────────────────────
echo
echo "--- negative-scenario coverage (plan acceptance: old evidence / PENDING evidence / corrupted NPZ / wrong commit) ---"
echo "  old evidence   : FULLY covered locally — verbatim-extracted append path + pre-placed stale PASS evidence (assertion a)"
echo "  PENDING evid.  : covered locally at append-logic level (a3 double-run distinguishability); full runner-level coverage needs the EDA simv"
echo "  corrupted NPZ  : covered locally at np.load API level + static no-guard check (c2); script-level handling needs VCS+cocotb"
echo "  wrong commit   : covered locally via static resume-path audit + wrong-commit npz behavioral proof (c3); end-to-end resume needs VCS+cocotb"

# ── Summary ───────────────────────────────────────────────────────────────
echo
echo "=== summary ==="
echo "assertions passed: $PASSES"
echo "assertions failed: $FAILURES"
if [ "$FAILURES" -gt 0 ]; then
    echo "TEST RESULT: RED — $FAILURES assertion(s) failed (expected before todo 9/11 fixes; must turn GREEN after provenance + safe-resume land)"
    exit 1
fi
echo "TEST RESULT: GREEN — all assertions passed"
exit 0
