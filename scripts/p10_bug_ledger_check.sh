#!/usr/bin/env bash
# =============================================================================
# p10_bug_ledger_check.sh — Phase 10 Todo 22 (Wave 5)
#
# Bug ledger dedup + Phase 10 evidence completeness check:
#   1. Detect duplicate bug entries in docs/bugs/bugs-soc-rtl.md
#      (same bug ID appearing with identical content more than once)
#   2. Verify every Phase 10 related bug entry carries a non-empty evidence
#      path whose file exists and is non-empty
#   3. Emit open_bugs / closed_bugs lists with their evidence paths
#
# Exit code:
#   0  — duplicate_count=0 AND every Phase 10 bug has valid evidence
#   1  — a duplicate or a missing/missing-file Phase 10 evidence found
#   2  — ledger or library unreachable (usage/environment error)
#
# The hard gate is scoped to Phase 10 related bugs (P9-00D / BUG-002 /
# WV-001), per the plan's acceptance criteria. Older entries whose
# Verification sections are prose (BUG-001/003/004) are listed with their
# evidence but are not gate-failing; missing referenced files on non-Phase-10
# entries are reported as warnings, not failures.
#
# Evidence:
#   build/evidence/task-22-phase10-rtl-verification.txt  (final report)
# =============================================================================
set -euo pipefail

source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

LEDGER="$REPO_ROOT/docs/bugs/bugs-soc-rtl.md"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
OUT_FILE="$EVIDENCE_DIR/task-22-phase10-rtl-verification.txt"
mkdir -p "$EVIDENCE_DIR"

COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "?")"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

failures=()
record_failure() { failures+=("$*"); }

# Phase 10 related bug IDs -> their Phase 10 evidence file (repo-relative).
# Mapping matches the plan's todo 22 references:
#   P9-00D  (PERF residual / PERF-06)   -> todo 8  evidence
#   BUG-002 (DRAM 8 MB window)          -> todo 19 evidence
#   WV-001  (SFU wrapper output)        -> todo 18 evidence
#   008     (DESC_BASE vs command ring) -> todo 13 L0L19 probe evidence
declare -A P10_EVIDENCE
P10_EVIDENCE[BUG-RTL-SOC-P9-00D]="build/evidence/task-8-phase10-rtl-verification.txt"
P10_EVIDENCE[BUG-RTL-SOC-002]="build/evidence/task-19-phase10-rtl-verification.txt"
P10_EVIDENCE[BUG-RTL-SOC-WV-001]="build/evidence/task-18-phase10-rtl-verification.txt"
P10_EVIDENCE[BUG-RTL-SOC-008]="build/evidence/l0l19-probe-evidence.txt"

# =============================================================================
# parse_ledger — extract per-bug records from the markdown ledger.
# Emits TSV lines:  id<TAB>status<TAB>evidence_paths(space-joined)
# Records are split on heading lines '^#{2,3} BUG' (both '### BUG-...' and the
# legacy '## BUG-...' headings). Evidence paths are normalized to
# repo-relative paths; absolute /home/prj/...CaduceusCore/ prefixes and
# ':line-range' suffixes are stripped.
# =============================================================================
parse_ledger() {
awk '
  function normalize_path(p,   o) {
    # strip absolute prefix down to repo-relative
    if (p ~ /CaduceusCore\//) sub(/^.*CaduceusCore\//, "", p)
    # strip :NN or :NN-NN line references
    sub(/:[0-9]+(-[0-9]+)?$/, "", p)
    # strip trailing punctuation
    sub(/[,.);]*$/, "", p)
    return p
  }
  function emit_block(buf, id, status, ev, s, p) {
    status = ""
    if (match(buf, /\*\*Status\*\* \| *[^|]+ */)) {
      s = substr(buf, RSTART, RLENGTH)
      gsub(/\*\*Status\*\* \| */, "", s)
      gsub(/ *\|/, "", s)
      gsub(/^ +| +$/, "", s)
      status = s
    }
    ev = ""
    while (match(buf, /([A-Za-z0-9_.\/-]+(\.txt|\.jsonl?|\.md|\.log)[A-Za-z0-9_.*\/:()-]*)/)) {
      p = normalize_path(substr(buf, RSTART, RLENGTH))
      if (ev != "") {
        if ((" " ev " ") !~ (" " p " ")) ev = ev " " p
      } else {
        ev = p
      }
      buf = substr(buf, RSTART + RLENGTH)
    }
    printf "%s\t%s\t%s\n", id, status, ev
  }
  {
    if ($0 ~ /^#{2,3} BUG/) {
      if (head != "" && buf != "") {
        id = head
        sub(/^#{2,3} +/, "", id)
        sub(/[ \t].*/, "", id)
        emit_block(buf, id)
      }
      head = $0
      buf = $0 "\n"
      next
    }
    if (head != "") buf = buf $0 "\n"
  }
  END {
    if (head != "" && buf != "") {
      id = head
      sub(/^#{2,3} +/, "", id)
      sub(/[ \t].*/, "", id)
      emit_block(buf, id)
    }
  }
' "$LEDGER"
}

[ -f "$LEDGER" ] || { echo "ERROR: ledger not found: $LEDGER" >&2; exit 2; }

# Parse into temp file; tolerate SIGPIPE-style mid-file quirks by re-checking.
TMP_TSV="$(mktemp "${EVIDENCE_DIR}/.task-22-parse.XXXXXX")"
trap 'rm -f "$TMP_TSV"' EXIT
parse_ledger > "$TMP_TSV"
if [ ! -s "$TMP_TSV" ]; then
  echo "ERROR: ledger parse produced no records (heading regex mismatch?)" >&2
  exit 2
fi

# =============================================================================
# 1. Duplicate detection — identical-content entries sharing one bug ID.
#    (A bug ID with multiple DIFFERENT entries is an ID collision; it is
#     reported but not counted as a duplicate — out of this todo's scope.)
# =============================================================================
declare -A SEEN_IDS        # id -> count of identical blocks
declare -A COLLISION_IDS   # id -> 1 when entries differ
declare -A BLOCK_HASH      # id -> sha256 of first block (identicality check)
duplicate_count=0

while IFS=$'\t' read -r id status ev; do
  if [ -n "${SEEN_IDS[$id]:-}" ]; then
    # Same ID seen before. Compare: this block == previous identical block?
    # We track identicality by hash; store first block text per id.
    if [ "${BLOCK_HASH[$id]:-}" = "$(printf '%s' "$id|$status|$ev" | sha256sum | cut -d' ' -f1)" ]; then
      SEEN_IDS[$id]=$((SEEN_IDS[$id] + 1))
    else
      COLLISION_IDS[$id]=1
    fi
  else
    SEEN_IDS[$id]=1
    BLOCK_HASH[$id]="$(printf '%s' "$id|$status|$ev" | sha256sum | cut -d' ' -f1)"
  fi
done < "$TMP_TSV"

for id in "${!SEEN_IDS[@]}"; do
  n="${SEEN_IDS[$id]}"
  if [ "$n" -gt 1 ]; then
    duplicate_count=$((duplicate_count + n - 1))
    record_failure "duplicate bug entry: ${id} appears ${n}x with identical content"
  fi
done

# =============================================================================
# 2. Phase 10 evidence verification — every Phase 10 bug must have a
#    non-empty evidence path and at least one referenced file must exist
#    and be non-empty (size > 0).
# =============================================================================
declare -A BUG_EVIDENCE BUG_STATUS
while IFS=$'\t' read -r id status ev; do
  BUG_STATUS[$id]="${status:-UNKNOWN}"
  BUG_EVIDENCE[$id]="${ev:-}"
done < "$TMP_TSV"

for id in "${!P10_EVIDENCE[@]}"; do
  expected="${P10_EVIDENCE[$id]}"
  ev="${BUG_EVIDENCE[$id]:-}"

  if [ -z "$ev" ]; then
    record_failure "Phase 10 bug ${id}: NO evidence path in ledger entry"
    continue
  fi
  # The expected Phase 10 task file must be among the referenced paths.
  case " $ev " in
    *" $expected "*)
      ;;
    *)
      record_failure "Phase 10 bug ${id}: expected evidence '${expected}' missing from entry paths: ${ev}"
      ;;
  esac
  # At least one referenced evidence file must exist and be non-empty.
  found_ok=0
  for p in $ev; do
    # Glob patterns (e.g. ph9-probe-*.jsonl) resolve to existing matches.
    # shellcheck disable=SC2086
    if ls "$REPO_ROOT"/${p} >/dev/null 2>&1; then
      if [ -s "$REPO_ROOT/${p}" ]; then
        found_ok=1
      fi
    fi
  done
  if [ "$found_ok" -eq 0 ]; then
    record_failure "Phase 10 bug ${id}: no referenced evidence file exists and is non-empty (paths: ${ev})"
  fi
done

# =============================================================================
# 3. Build open_bugs / closed_bugs lists with evidence paths.
# =============================================================================
open_bugs=""
closed_bugs=""
for id in "${!BUG_STATUS[@]}"; do
  line="${id} [status=${BUG_STATUS[$id]}] evidence: ${BUG_EVIDENCE[$id]:-NONE}"
  case "${BUG_STATUS[$id]}" in
    *[Ff]ixed|*[Cc]losed|*[Rr]esolved)
      closed_bugs="${closed_bugs}${line}\n"
      ;;
    *)
      open_bugs="${open_bugs}${line}\n"
      ;;
  esac
done

# =============================================================================
# 3b. Warning pass — missing referenced files on NON-Phase-10 entries.
# =============================================================================
warnings=""
for id in "${!BUG_EVIDENCE[@]}"; do
  [ -n "${P10_EVIDENCE[$id]:-}" ] && continue   # Phase 10 handled above
  for p in ${BUG_EVIDENCE[$id]:-}; do
    if ! ls "$REPO_ROOT"/${p} >/dev/null 2>&1; then
      warnings="${warnings}warning: ${id} references missing file: ${p}\n"
    elif [ ! -s "$REPO_ROOT/${p}" ]; then
      warnings="${warnings}warning: ${id} references empty file: ${p}\n"
    fi
  done
done

# =============================================================================
# 4. Verdict + evidence file
# =============================================================================
VERDICT="PASS"
if [ "$duplicate_count" -gt 0 ] || [ "${#failures[@]}" -gt 0 ]; then
  VERDICT="FAIL"
fi

{
  echo "Task 22 - Phase 10 RTL Verification: Bug ledger dedup + completeness check"
  echo "==========================================================================="
  echo "Timestamp     : ${TS}"
  echo "Commit        : ${COMMIT}"
  echo "Ledger        : ${LEDGER}"
  echo ""
echo "duplicate_count=${duplicate_count}"
echo ""
if [ "${#COLLISION_IDS[@]}" -gt 0 ]; then
  echo "ID collisions (same ID, DIFFERENT content — pre-existing ledger quirk, NOT the P9-00D duplicate; informational):"
  for cid in "${!COLLISION_IDS[@]}"; do
    echo "  ${cid}"
  done
  echo ""
fi
echo "Open bugs:"
  if [ -n "$open_bugs" ]; then printf "%b" "$open_bugs"; else echo "  (none)"; fi
  echo ""
  echo "Closed bugs:"
  if [ -n "$closed_bugs" ]; then printf "%b" "$closed_bugs"; else echo "  (none)"; fi
  echo ""
  if [ -n "${warnings:-}" ]; then
    echo "Warnings (non-Phase-10 entries, informational):"
    printf "%b" "$warnings"
    echo ""
  fi
  echo "Phase 10 evidence mapping (bug -> expected evidence file):"
  for id in "${!P10_EVIDENCE[@]}"; do
    echo "  ${id} -> ${P10_EVIDENCE[$id]}"
  done
  echo ""
  if [ "${#failures[@]}" -gt 0 ]; then
    echo "Failures:"
    for f in "${failures[@]}"; do
      echo "  - $f"
    done
    echo ""
  fi
  echo "Verification: ${VERDICT}"
  echo "  duplicate_count=$duplicate_count (must be 0)"
  echo "  phase10_evidence_complete=$([ "${#failures[@]}" -eq 0 ] && echo yes || echo no)"
  echo ""
  echo "Result: ${VERDICT}"
} > "$OUT_FILE"

if [ "$VERDICT" = "FAIL" ]; then
  cat "$OUT_FILE" >&2
  exit 1
fi

echo "[p10_bug_ledger_check] duplicate_count=${duplicate_count}, phase10 evidence complete, exit 0"
exit 0
