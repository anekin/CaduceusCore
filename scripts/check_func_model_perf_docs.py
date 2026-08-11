#!/usr/bin/env python3
"""Semantic checker for Func Model performance docs and bug ledger.

Validates that performance documentation:
- does not claim cycle-accurate / RTL-calibrated / measured-cycles status,
- uses the corrected Qwen2.5-3B dimensions,
- documents assumptions, uncertainty, and future RTL calibration phase,
- does not treat report-only KPIs as product gates without report-only language.

Also validates that the bug ledger records the two required deferred-scope
entries (non-Block engines and GMMA dead constant) and contains zero waivers.

Usage:
    python3 scripts/check_func_model_perf_docs.py \
        --spec config/func_model_perf_spec_v1.json \
        --bugs docs/bugs/bugs-soc-func-model.md

    python3 scripts/check_func_model_perf_docs.py \
        --negative-fixtures \
        config/tests/docs_cycle_accurate.md,config/tests/docs_old_qwen.md,config/tests/docs_kpi_gate.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# Docs that must be checked for forbidden overclaim language.
_REQUIRED_DOCS = frozenset({
    "docs/arc_vs_func.md",
    "docs/func_model_architecture.md",
    "docs/func-model-e2e-performance-analysis.md",
    "docs/func_model_performance_analysis.md",
})

# Forbidden phrases that imply calibrated/cycle-accurate status.
_FORBIDDEN_PHRASES = {
    "cycle-accurate": "Func Model performance is estimated, not cycle-accurate",
    "cycle accurate": "Func Model performance is estimated, not cycle-accurate",
    "rtl-calibrated": "No RTL calibration data exists yet; use architecture_assumption/uncalibrated",
    "rtl calibrated": "No RTL calibration data exists yet; use architecture_assumption/uncalibrated",
    "measured cycles": "All Func Model outputs are estimated_cycles, not measured",
}

# Correct Qwen2.5-3B parameters (T13 canonical).
_CORRECT_QWEN_DIMS = {
    "hidden": 2048,
    "intermediate": 11008,
    "layers": 36,
    "heads": 16,
    "kv_heads": 2,
    "head_dim": 128,
    "kv_dim": 256,
}

# Stale parameter values that must not appear un-annotated in docs.
_STALE_QWEN_VALUES = {
    "hidden=2560",
    "intermediate=9728",
    "layers=28",
    "num_heads=32",
    "kv_heads=16",
    "kv_dim=2048",
    "hidden_size=2560",
    "intermediate_size=9728",
    "num_hidden_layers=28",
    "num_attention_heads=32",
    "num_key_value_heads=16",
}

# Required language markers in the checked docs.
_REQUIRED_MARKERS = [
    ("estimated_cycles", "Docs must mention estimated_cycles"),
    ("architecture_assumption", "Docs must mention architecture_assumption"),
    ("uncalibrated", "Docs must mention uncalibrated calibration state"),
    ("uncertainty", "Docs must document uncertainty bands"),
    ("future RTL calibration", "Docs must mention future RTL calibration phase"),
]

# Bug ledger required deferred-scope entries.
_REQUIRED_DEFERRED_ENTRIES = [
    ("BUG-SOC-FM-009", "BUG-SOC-FM-009 (non-Block engine deferred-scope) entry missing"),
    ("BUG-SOC-FM-010", "BUG-SOC-FM-010 (GMMA dead constant) deferred entry missing"),
]


@dataclass
class Finding:
    file: str
    line: int
    category: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "category": self.category,
            "message": self.message,
        }


@dataclass
class DocCheckResult:
    file: str
    findings: List[Finding] = field(default_factory=list)
    lines_checked: int = 0

    @property
    def valid(self) -> bool:
        return len(self.findings) == 0


@dataclass
class BugLedgerResult:
    file: str
    findings: List[Finding] = field(default_factory=list)
    deferred_present: List[str] = field(default_factory=list)
    waivers: int = 0
    blocking_open: int = 0

    @property
    def valid(self) -> bool:
        return len(self.findings) == 0


@dataclass
class RunResult:
    docs_valid: bool = True
    bugs_valid: bool = True
    negative_fixtures: List[Dict[str, Any]] = field(default_factory=list)
    doc_results: List[DocCheckResult] = field(default_factory=list)
    bug_result: Optional[BugLedgerResult] = None

    @property
    def valid(self) -> bool:
        if self.negative_fixtures:
            return all(f.get("fixture_passes", False) for f in self.negative_fixtures)
        return self.docs_valid and self.bugs_valid


def _read_text_file(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _line_number(text: str, offset: int) -> int:
    return text[:offset].count("\n") + 1


_IGNORE_ANNOTATION = re.compile(r"<!--\s*doc-check:\s*ignore\s*-->")


def _line_has_ignore_annotation(text: str, offset: int) -> bool:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    line = text[start:end]
    return bool(_IGNORE_ANNOTATION.search(line))


def _scan_forbidden_phrases(text: str, filename: str) -> List[Finding]:
    findings: List[Finding] = []
    lowered = text.lower()
    for phrase, reason in _FORBIDDEN_PHRASES.items():
        for match in re.finditer(re.escape(phrase), lowered):
            if _line_has_ignore_annotation(text, match.start()):
                continue
            line = _line_number(text, match.start())
            findings.append(Finding(filename, line, "forbidden_phrase", f"{phrase!r}: {reason}"))
    return findings


def _scan_stale_qwen_values(text: str, filename: str) -> List[Finding]:
    findings: List[Finding] = []
    for stale in _STALE_QWEN_VALUES:
        # Use word boundary at the start so "kv_dim=2048" does not match inside "qkv_dim=2048".
        pattern = r"(?:^|\b|\s)" + re.escape(stale)
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _line_has_ignore_annotation(text, match.start()):
                continue
            line = _line_number(text, match.start())
            findings.append(
                Finding(
                    filename,
                    line,
                    "stale_qwen_dims",
                    f"Stale Qwen parameter {stale!r} found; use corrected T13 dimensions",
                )
            )
    return findings


def _scan_kpi_gate_overclaim(text: str, filename: str) -> List[Finding]:
    """Detect KPI-as-gate language when the doc does not qualify it as report-only."""
    findings: List[Finding] = []
    lowered = text.lower()

    # Heuristic: a hard product gate is implied by "approved for production",
    # "hard KPI gate", "block tape-out", or "must block" combined with numeric KPIs.
    gate_indicators = [
        "approved for production",
        "hard kpi gate",
        "block tape-out",
        "must block",
        "production signoff",
    ]
    report_only_indicators = [
        "report-only",
        "report only",
        "estimated_cycles",
        "uncalibrated",
        "architecture_assumption",
        "future rtl calibration",
    ]

    has_report_only = any(ind in lowered for ind in report_only_indicators)
    for ind in gate_indicators:
        for match in re.finditer(re.escape(ind), lowered):
            if _line_has_ignore_annotation(text, match.start()):
                continue
            if not has_report_only:
                line = _line_number(text, match.start())
                findings.append(
                    Finding(
                        filename,
                        line,
                        "kpi_gate_overclaim",
                        f"KPI-as-gate language {ind!r} without report-only/uncalibrated qualification",
                    )
                )
            break  # one finding per indicator is enough
    return findings


def _scan_required_markers(text: str, filename: str) -> List[Finding]:
    findings: List[Finding] = []
    lowered = text.lower()
    for marker, reason in _REQUIRED_MARKERS:
        if marker.lower() not in lowered:
            findings.append(Finding(filename, 0, "missing_marker", reason))
    return findings


def check_doc_file(path: Path) -> DocCheckResult:
    text = _read_text_file(path)
    rel = _relative(path)
    result = DocCheckResult(file=rel, lines_checked=text.count("\n") + 1)
    result.findings.extend(_scan_forbidden_phrases(text, rel))
    result.findings.extend(_scan_stale_qwen_values(text, rel))
    result.findings.extend(_scan_kpi_gate_overclaim(text, rel))
    result.findings.extend(_scan_required_markers(text, rel))
    return result


def check_all_docs(extra_paths: Optional[List[Path]] = None) -> List[DocCheckResult]:
    results: List[DocCheckResult] = []
    for rel in _REQUIRED_DOCS:
        path = REPO_ROOT / rel
        if path.is_file():
            results.append(check_doc_file(path))
        else:
            results.append(
                DocCheckResult(
                    file=rel,
                    findings=[Finding(rel, 0, "missing_file", "Required doc file not found")],
                )
            )
    if extra_paths:
        for p in extra_paths:
            results.append(check_doc_file(p))
    return results


def check_bug_ledger_from_text(text: str, filename: str) -> BugLedgerResult:
    """Check bug ledger content passed as text (used by tests)."""
    result = BugLedgerResult(file=filename)

    for bug_id, reason in _REQUIRED_DEFERRED_ENTRIES:
        if bug_id in text:
            result.deferred_present.append(bug_id)
        else:
            result.findings.append(Finding(filename, 0, "missing_deferred_entry", reason))

    waiver_pattern = re.compile(r"\bwaiv(?:e|ed|al|er)\b", re.IGNORECASE)
    waiver_lines: List[Tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if waiver_pattern.search(line):
            waiver_lines.append((i, line.strip()))
    result.waivers = len(waiver_lines)
    for line_no, line_text in waiver_lines:
        result.findings.append(
            Finding(filename, line_no, "waiver_found", f"Waiver language detected: {line_text[:80]}")
        )

    for i, line in enumerate(text.splitlines(), start=1):
        if re.search(r"\*\*Status\*\*:\s*(Open|New|Blocking)", line, re.IGNORECASE):
            if "deferred" not in line.lower() and "info" not in line.lower():
                result.blocking_open += 1
                result.findings.append(
                    Finding(filename, i, "blocking_open_defect", f"Blocking open defect: {line.strip()}")
                )

    return result


def check_bug_ledger(path: Path) -> BugLedgerResult:
    text = _read_text_file(path)
    rel = _relative(path)
    return check_bug_ledger_from_text(text, rel)


def check_negative_fixture(path: Path) -> Dict[str, Any]:
    """A negative fixture passes if the checker finds at least one violation."""
    rel = _relative(path)
    result = check_doc_file(path)
    rejected = len(result.findings)
    fixture_passes = rejected > 0
    return {
        "file": rel,
        "expected_reject": True,
        "fixture_passes": fixture_passes,
        "rejected": rejected,
        "accepted": 1 if rejected == 0 else 0,
        "findings": [f.to_dict() for f in result.findings],
        "reason": "Negative fixture correctly rejected" if fixture_passes else "Negative fixture was accepted",
    }


def _aggregate_doc_result(results: List[DocCheckResult]) -> Dict[str, Any]:
    total_findings = sum(len(r.findings) for r in results)
    files_with_findings = sum(1 for r in results if not r.valid)
    return {
        "valid": total_findings == 0,
        "files_checked": len(results),
        "files_with_findings": files_with_findings,
        "total_findings": total_findings,
        "results": [
            {
                "file": r.file,
                "valid": r.valid,
                "lines_checked": r.lines_checked,
                "findings": [f.to_dict() for f in r.findings],
            }
            for r in results
        ],
    }


def _bug_result_to_dict(result: BugLedgerResult) -> Dict[str, Any]:
    return {
        "valid": result.valid,
        "file": result.file,
        "deferred_present": result.deferred_present,
        "waivers": result.waivers,
        "blocking_open": result.blocking_open,
        "findings": [f.to_dict() for f in result.findings],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check Func Model performance docs and bug ledger for overclaim/stale data",
    )
    parser.add_argument(
        "--spec",
        type=str,
        default=None,
        help="Path to the performance spec JSON (used for structural validation; optional)",
    )
    parser.add_argument(
        "--bugs",
        type=str,
        default="docs/bugs/bugs-soc-func-model.md",
        help="Path to the Func Model bug ledger markdown",
    )
    parser.add_argument(
        "--negative-fixtures",
        type=str,
        default=None,
        help="Comma-separated paths to markdown fixtures that must be rejected",
    )
    parser.add_argument(
        "--extra-docs",
        type=str,
        default=None,
        help="Comma-separated extra markdown docs to check in addition to the required set",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        default=False,
        help="Output verdict as JSON",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress non-JSON output",
    )
    args = parser.parse_args()

    results: Dict[str, Any] = {}
    exit_code = 0

    if args.negative_fixtures:
        fixture_paths = [p.strip() for p in args.negative_fixtures.split(",") if p.strip()]
        fixture_results: List[Dict[str, Any]] = []
        total_rejected = 0
        total_accepted = 0
        for fp in fixture_paths:
            path = Path(fp)
            if not path.is_absolute():
                path = REPO_ROOT / path
            fr = check_negative_fixture(path)
            fixture_results.append(fr)
            total_rejected += fr["rejected"]
            total_accepted += fr["accepted"]
            if not fr["fixture_passes"]:
                exit_code = 1
        results["negative_fixtures"] = fixture_results
        results["summary"] = {
            "total_fixtures": len(fixture_results),
            "rejected": sum(1 for f in fixture_results if f["fixture_passes"]),
            "accepted": sum(1 for f in fixture_results if not f["fixture_passes"]),
        }
        if results["summary"]["rejected"] != len(fixture_results) or results["summary"]["accepted"] != 0:
            exit_code = 1
    else:
        # Normal mode: check required docs + bug ledger
        extra_paths: Optional[List[Path]] = None
        if args.extra_docs:
            extra_paths = []
            for p in args.extra_docs.split(","):
                p = p.strip()
                if not p:
                    continue
                path = Path(p)
                if not path.is_absolute():
                    path = REPO_ROOT / path
                extra_paths.append(path)

        doc_results = check_all_docs(extra_paths)
        doc_summary = _aggregate_doc_result(doc_results)
        results["docs"] = doc_summary
        if not doc_summary["valid"]:
            exit_code = 1

        bug_path = Path(args.bugs)
        if not bug_path.is_absolute():
            bug_path = REPO_ROOT / bug_path
        if bug_path.is_file():
            bug_result = check_bug_ledger(bug_path)
            results["bugs"] = _bug_result_to_dict(bug_result)
            if not bug_result.valid:
                exit_code = 1
        else:
            results["bugs"] = {
                "valid": False,
                "file": str(bug_path),
                "findings": [{"file": str(bug_path), "line": 0, "category": "missing_file", "message": "Bug ledger not found"}],
            }
            exit_code = 1

        # Optional spec structural check (if provided)
        if args.spec:
            spec_path = Path(args.spec)
            if not spec_path.is_absolute():
                spec_path = REPO_ROOT / spec_path
            try:
                with open(spec_path, "r", encoding="utf-8") as f:
                    spec_data = json.load(f)
                spec_info = {
                    "valid": True,
                    "spec_id": spec_data.get("spec_id", "unknown"),
                    "schema_version": spec_data.get("schema_version", "unknown"),
                    "basis_policy": spec_data.get("frozen_policies", {}).get("basis", ""),
                }
            except (json.JSONDecodeError, FileNotFoundError) as e:
                spec_info = {"valid": False, "error": str(e)}
                exit_code = 1
            results["spec"] = spec_info

        results["summary"] = {
            "docs_valid": results.get("docs", {}).get("valid", False),
            "bugs_valid": results.get("bugs", {}).get("valid", False),
            "blocking_open_defects": results.get("bugs", {}).get("blocking_open", 0),
            "deferred_entries_present": results.get("bugs", {}).get("deferred_present", []),
            "waivers": results.get("bugs", {}).get("waivers", 0),
        }

    if args.json_output or args.quiet:
        print(json.dumps(results, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        _print_pretty(results)

    return exit_code


def _print_pretty(results: Dict[str, Any]) -> None:
    summary = results.get("summary", {})
    if "negative_fixtures" in results:
        print("Negative fixture validation")
        print(f"  Total: {summary.get('total_fixtures', 0)}")
        print(f"  rejected={summary.get('rejected', 0)},accepted={summary.get('accepted', 0)}")
        for f in results["negative_fixtures"]:
            status = "OK" if f["fixture_passes"] else "BAD"
            print(f"  [{status}] {f['file']}: findings={f['rejected']}, accepted={f['accepted']}")
            for finding in f.get("findings", [])[:3]:
                print(f"      line {finding['line']}: [{finding['category']}] {finding['message']}")
        return

    print("Func Model performance doc consistency check")
    docs = results.get("docs", {})
    print(f"  Docs: {docs.get('files_checked', 0)} files checked, {docs.get('total_findings', 0)} findings")
    for r in docs.get("results", []):
        status = "PASS" if r["valid"] else "FAIL"
        print(f"    [{status}] {r['file']} ({r['lines_checked']} lines)")
        for f in r.get("findings", []):
            print(f"      line {f['line']}: [{f['category']}] {f['message']}")

    bugs = results.get("bugs", {})
    status = "PASS" if bugs.get("valid") else "FAIL"
    print(f"  Bug ledger: {status}")
    print(f"    Deferred entries present: {bugs.get('deferred_present', [])}")
    print(f"    Waivers: {bugs.get('waivers', 0)}")
    print(f"    Blocking open defects: {bugs.get('blocking_open', 0)}")
    for f in bugs.get("findings", []):
        print(f"      line {f['line']}: [{f['category']}] {f['message']}")

    if "spec" in results:
        spec = results["spec"]
        status = "PASS" if spec.get("valid") else "FAIL"
        print(f"  Spec: {status} — {spec.get('spec_id', '?')} v{spec.get('schema_version', '?')}")
        if "error" in spec:
            print(f"    Error: {spec['error']}")

    print(f"\nSummary: docs_valid={summary.get('docs_valid')}, bugs_valid={summary.get('bugs_valid')}, "
          f"blocking_open={summary.get('blocking_open_defects', 0)}, waivers={summary.get('waivers', 0)}")


if __name__ == "__main__":
    sys.exit(main())
