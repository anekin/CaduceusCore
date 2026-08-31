#!/usr/bin/env python3
# =============================================================================
# check_evidence_provenance.py — verify an evidence file's provenance header
# (plan soc-rtl-review-remediation todo 11, Metis C3 executable definition)
# =============================================================================
# Reads an evidence file, extracts its provenance block
# (provenance_begin .. provenance_end, produced by
# scripts/gen_evidence_provenance.py), and verifies:
#
#   1. the header is present and complete (begin + end markers),
#   2. every recorded artifact hash matches the file currently on disk
#      (simv / flist / driver / firmware sha256 are recomputed; golden is
#      re-aggregated over its directory),
#   3. the recorded git commit equals the current HEAD.
#
# Exit codes (Metis C3):
#   0  VERIFIED — header present, all recorded hashes match the current build
#   1  REJECTED — header missing, truncated, commit mismatch, or a recorded
#                 hash does not match the current build
#   2  USAGE     — bad arguments / unreadable evidence file
#
# Artifacts the generator honestly recorded as `missing` are re-checked: if
# the file now exists the evidence is stale relative to the build and the
# check fails in --strict mode (default: warning only, since the snapshot may
# legitimately predate the artifact, e.g. the first-run checkpoint npz).
#
# Usage:
#   python3 scripts/check_evidence_provenance.py build/evidence/task-14-....txt
#   python3 scripts/check_evidence_provenance.py --strict evidence.txt
#   python3 scripts/check_evidence_provenance.py --skip checkpoint evidence.txt
# =============================================================================

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BEGIN_MARK = "provenance_begin"
END_MARK = "provenance_end"

# Hash field -> (path field, aggregate mode).  All hashes are SHA-256.
HASH_FIELDS = {
    "provenance_simv_sha256": ("provenance_simv_path", False),
    "provenance_flist_sha256": ("provenance_flist_path", False),
    "provenance_driver_sha256": ("provenance_driver_path", False),
    "provenance_firmware_sha256": ("provenance_firmware_path", False),
    "provenance_golden_sha256": ("provenance_golden_path", True),
    "provenance_checkpoint_sha256": ("provenance_checkpoint_path", False),
}


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dir(path: Path) -> str:
    if not path.is_dir():
        return ""
    entries = []
    for p in sorted(path.rglob("*")):
        if not p.is_file():
            continue
        dig = _sha256_file(p)
        if not dig:
            return ""
        entries.append((str(p.relative_to(path)), dig))
    if not entries:
        return ""
    h = hashlib.sha256()
    for rel, dig in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(dig.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _current_head() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _parse_block(text: str) -> dict:
    """Parse the provenance block into a dict; {} when absent/truncated."""
    fields: dict = {}
    in_block = False
    for line in text.splitlines():
        if line.strip() == BEGIN_MARK:
            in_block = True
            continue
        if line.strip() == END_MARK:
            return fields if in_block else {}
        if in_block and "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    return {}  # reached EOF without provenance_end -> truncated


def verify(text: str, strict: bool, skip: set) -> tuple:
    """Return (ok: bool, messages: list[str])."""
    messages: list = []
    fields = _parse_block(text)
    if not fields:
        return False, ["no complete provenance header "
                       "(provenance_begin..provenance_end) in evidence"]

    # Commit binding: recorded commit must equal current HEAD.
    recorded = fields.get("provenance_git_commit", "")
    head = _current_head()
    if recorded == "unknown" or (head and recorded != head):
        messages.append(
            f"commit binding mismatch: evidence commit {recorded[:12]} "
            f"!= current HEAD {head[:12] if head else 'unknown'}")
        return False, messages
    if not head:
        messages.append("warning: cannot resolve current git HEAD; "
                        "commit check skipped")

    for hash_key, (path_key, is_dir) in HASH_FIELDS.items():
        if hash_key in skip:
            continue
        if hash_key not in fields:
            messages.append(f"missing hash field {hash_key} in header")
            return False, messages
        recorded_hash = fields[hash_key]
        path_str = fields.get(path_key, "")
        if recorded_hash == "missing":
            if path_str and Path(path_str).exists():
                msg = (f"{hash_key} recorded 'missing' but {path_str} now "
                       f"exists — evidence predates the artifact")
                if strict:
                    messages.append(msg)
                    return False, messages
                messages.append(f"warning: {msg}")
            continue
        if not path_str:
            messages.append(f"{hash_key} has a hash but no path — cannot verify")
            return False, messages
        current = (_sha256_dir(Path(path_str)) if is_dir
                   else _sha256_file(Path(path_str)))
        if not current:
            messages.append(f"{hash_key}: artifact not on disk ({path_str}) — "
                            f"recorded {recorded_hash}")
            return False, messages
        if current != recorded_hash:
            messages.append(
                f"{hash_key} MISMATCH: evidence {recorded_hash[:12]} vs "
                f"current build {current[:12]} ({path_str})")
            return False, messages
    return True, messages


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an evidence file's hash-bound provenance header "
                    "against the current build (todo 11).")
    parser.add_argument("evidence", help="path to the evidence file")
    parser.add_argument("--strict", action="store_true",
                        help="treat 'missing'-recorded artifacts that now "
                             "exist as REJECTED (default: warning only)")
    parser.add_argument("--skip", action="append", default=[],
                        help="hash field class to skip (e.g. checkpoint); "
                             "repeatable")
    args = parser.parse_args()

    ev = Path(args.evidence)
    if not ev.is_file():
        print(f"ERROR: evidence file not found: {ev}", file=sys.stderr)
        return 2
    try:
        text = ev.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: cannot read {ev}: {exc}", file=sys.stderr)
        return 2

    ok, messages = verify(text, args.strict, set(args.skip))
    for msg in messages:
        print(f"[provenance-check] {msg}", file=sys.stderr)
    if ok:
        print(f"[provenance-check] VERIFIED: {ev}")
        return 0
    print(f"[provenance-check] REJECTED: {ev}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
