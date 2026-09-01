#!/usr/bin/env python3
# =============================================================================
# gen_evidence_provenance.py — hash-bound provenance block for one RTL run
# (plan soc-rtl-review-remediation todo 11)
# =============================================================================
# Generates a provenance block binding one RTL run's evidence to the exact
# build artifacts it exercised, all SHA-256:
#
#   * git HEAD + `git status --porcelain` dirty state (provenance_git_dirty)
#   * simv path + sha256
#   * RTL flist content sha256
#   * python driver file sha256
#   * firmware ELF/HEX sha256
#   * golden / checkpoint file sha256 (golden may be a directory -> aggregate
#     hash over its sorted file list)
#   * tool versions: VCS (`vcs -ID`), cocotb, Python, riscv64-unknown-elf-gcc,
#     GNU timeout, Spike
#   * timestamp + run id
#
# Output is a stable-key header block suitable for prepending to evidence
# files:
#
#   provenance_begin
#   provenance_run_id=...
#   provenance_timestamp=...
#   provenance_git_commit=...
#   provenance_git_dirty=true|false
#   provenance_simv_path=...    provenance_simv_sha256=...
#   provenance_flist_sha256=...
#   provenance_driver_sha256=...
#   provenance_firmware_sha256=...
#   provenance_golden_sha256=...
#   provenance_checkpoint_sha256=...
#   provenance_tool_versions=vcs=...;cocotb=...;python=...;...
#   provenance_end
#
# Missing artifacts are recorded honestly as `<key>=missing` (never fabricated
# hashes); the block is still emitted so absence is visible.  The check side
# lives in scripts/check_evidence_provenance.py.
#
# Snapshot timing contract (todo 11 / Oracle round-5): callers MUST capture
# this block AFTER the firmware is rebuilt and the simv is compiled, but
# BEFORE the simulator starts — otherwise the recorded hashes do not describe
# the binaries the run actually exercised.
#
# Usage:
#   python3 scripts/gen_evidence_provenance.py --run-id RUN1 \
#       --simv build/ibex_segment_rtl/simv_soc_ibex_seg \
#       --flist rtl/soc/soc.flist \
#       --driver sim/rtl_soc_segment_run.py \
#       --firmware firmware/build/npu_firmware.hex \
#       --golden rtl/test_vectors/soc_e2e/qwen25-3b-36layer \
#       --checkpoint build/evidence/task-14-soc-rtl-verification-checkpoints.npz \
#       --out build/evidence/provenance-RUN1.txt
# =============================================================================

import argparse
import datetime
import hashlib
import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MISSING = "missing"

# Tool version probes.  Every probe is best-effort: a missing tool records
# "unavailable" instead of failing the block (provenance generation must never
# kill the run it describes).
TOOL_PROBES = [
    # VCS version via `vcs -ID` (short banner; enough to fingerprint a build).
    ("vcs", ["vcs", "-ID"]),
    ("cocotb", [sys.executable, "-c",
                "import cocotb;print(getattr(cocotb,'__version__','unknown'))"]),
    ("riscv64-unknown-elf-gcc", ["riscv64-unknown-elf-gcc", "--version"]),
    ("timeout", ["timeout", "--version"]),
    ("spike", ["spike", "--version"]),
]


def _sha256_file(path: Path) -> str:
    """SHA-256 of a file's bytes; MISSING if it does not exist / is a dir."""
    try:
        if not path.is_file():
            return MISSING
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return MISSING


def _sha256_dir(path: Path) -> str:
    """Aggregate SHA-256 over a directory's sorted file list.

    Deterministic: hash over (relpath, file-sha256) pairs so both content and
    membership changes alter the digest.  Used for golden vector directories.
    """
    try:
        if not path.is_dir():
            return MISSING
        entries = []
        for p in sorted(path.rglob("*")):
            if not p.is_file():
                continue
            dig = _sha256_file(p)
            if dig == MISSING:
                return MISSING
            entries.append((str(p.relative_to(path)), dig))
        if not entries:
            return MISSING
        h = hashlib.sha256()
        for rel, dig in entries:
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(dig.encode("ascii"))
            h.update(b"\n")
        return h.hexdigest()
    except OSError:
        return MISSING


def _git_state():
    """(commit, dirty, porcelain_count, porcelain_lines)."""
    commit = "unknown"
    dirty = "unknown"
    count = -1
    lines: list = []
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            commit = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
            count = len(lines)
            dirty = "true" if lines else "false"
    except (OSError, subprocess.SubprocessError):
        pass
    return commit, dirty, count, lines


def _tool_version(cmd: list) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.stdout.strip():
            return out.stdout.strip().splitlines()[0][:200]
        if out.returncode == 0 and out.stderr.strip():
            return out.stderr.strip().splitlines()[0][:200]
        return "unavailable" if out.returncode != 0 else "no-output"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _tool_versions() -> str:
    parts = [f"python={platform.python_version()}"]
    for name, cmd in TOOL_PROBES:
        parts.append(f"{name}={_tool_version(cmd)}")
    return ";".join(parts)


def generate(run_id: str, simv: str, flist: str, driver: str, firmware: str,
             golden: str, checkpoint: str) -> str:
    """Return the provenance block text (all keys stable, SHA-256 only)."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    commit, dirty, pcount, plines = _git_state()

    simv_path = Path(simv) if simv else None
    flist_path = Path(flist) if flist else None
    driver_path = Path(driver) if driver else None
    firmware_path = Path(firmware) if firmware else None
    golden_path = Path(golden) if golden else None
    ckpt_path = Path(checkpoint) if checkpoint else None

    simv_sha = _sha256_file(simv_path) if simv_path else MISSING
    flist_sha = _sha256_file(flist_path) if flist_path else MISSING
    driver_sha = _sha256_file(driver_path) if driver_path else MISSING
    firmware_sha = _sha256_file(firmware_path) if firmware_path else MISSING
    golden_sha = _sha256_dir(golden_path) if golden_path else MISSING
    ckpt_sha = _sha256_file(ckpt_path) if ckpt_path else MISSING

    lines = [
        "provenance_begin",
        f"provenance_run_id={run_id}",
        f"provenance_timestamp={now}",
        f"provenance_git_commit={commit}",
        f"provenance_git_dirty={dirty}",
        f"provenance_git_porcelain_count={pcount}",
    ]
    for ln in plines[:50]:
        lines.append(f"provenance_git_porcelain_line={ln}")
    lines += [
        f"provenance_simv_path={simv if simv else MISSING}",
        f"provenance_simv_sha256={simv_sha}",
        f"provenance_flist_path={flist if flist else MISSING}",
        f"provenance_flist_sha256={flist_sha}",
        f"provenance_driver_path={driver if driver else MISSING}",
        f"provenance_driver_sha256={driver_sha}",
        f"provenance_firmware_path={firmware if firmware else MISSING}",
        f"provenance_firmware_sha256={firmware_sha}",
        f"provenance_golden_path={golden if golden else MISSING}",
        f"provenance_golden_sha256={golden_sha}",
        f"provenance_checkpoint_path={checkpoint if checkpoint else MISSING}",
        f"provenance_checkpoint_sha256={ckpt_sha}",
        f"provenance_tool_versions={_tool_versions()}",
        "provenance_end",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a SHA-256 hash-bound provenance block for one "
                    "RTL run (todo 11).  Missing artifacts record "
                    "'hash=missing' honestly; the block is always emitted.")
    parser.add_argument("--run-id", default="unknown",
                        help="run identifier (stable key provenance_run_id)")
    parser.add_argument("--simv", default="", help="path to the simv binary")
    parser.add_argument("--flist", default="", help="path to the RTL flist")
    parser.add_argument("--driver", default="",
                        help="path to the python driver file")
    parser.add_argument("--firmware", default="",
                        help="path to the firmware ELF/HEX")
    parser.add_argument("--golden", default="",
                        help="path to golden file or golden vector directory")
    parser.add_argument("--checkpoint", default="",
                        help="path to the checkpoint npz (if any)")
    parser.add_argument("--out", default="",
                        help="write the block to this file (default: stdout)")
    args = parser.parse_args()

    block = generate(run_id=args.run_id, simv=args.simv, flist=args.flist,
                     driver=args.driver, firmware=args.firmware,
                     golden=args.golden, checkpoint=args.checkpoint)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(block, encoding="utf-8")
        print(f"[provenance] block written to {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
