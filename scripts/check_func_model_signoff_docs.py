#!/usr/bin/env python3
"""Semantic checker for Func Model signoff documentation.

Scans the signoff checklist and testcase list for overclaim:
any test name containing 'scaled' or 'single_tile' must NOT be
described as 'full-shape'.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

CHECKLIST_PATH = REPO_ROOT / "docs" / "func-model-signoff-checklist.md"
TESTCASE_LIST_PATH = REPO_ROOT / "rtl" / "testcase-list-soc-fm.md"

# Patterns for scaled/single-tile tests
SCALED_PATTERN = re.compile(r'\b\w*scaled\w*\b|\b\w*single_tile\w*\b', re.IGNORECASE)

# Full-shape description patterns
FULL_SHAPE_PATTERN = re.compile(r'\bfull[-_ ]?shape\b', re.IGNORECASE)


def load_markdown(path: Path) -> Optional[str]:
    """Load markdown file content, returning None if missing."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def extract_test_names(content: str) -> List[str]:
    """Extract test function names from markdown content."""
    return re.findall(r'\b(test_\w+)\b', content)


def find_lines_with_test(content: str, test_name: str) -> List[str]:
    """Return lines containing the given test name."""
    return [line for line in content.splitlines() if test_name in line]


def check_scaled_labels(
    checklist_text: Optional[str] = None,
    testcase_text: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """Check for overclaim: scaled/single_tile tests described as full-shape.

    Returns (passed, issues_list).
    """
    issues: List[str] = []

    texts_to_check: List[Tuple[str, str]] = []
    if checklist_text is not None:
        texts_to_check.append(("docs/func-model-signoff-checklist.md", checklist_text))
    if testcase_text is not None:
        texts_to_check.append(("rtl/testcase-list-soc-fm.md", testcase_text))

    for doc_name, text in texts_to_check:
        test_names = extract_test_names(text)
        # Find scaled/single_tile test names
        scaled_tests = [t for t in test_names if SCALED_PATTERN.search(t)]

        for test_name in set(scaled_tests):  # deduplicate
            lines = find_lines_with_test(text, test_name)
            for line in lines:
                if FULL_SHAPE_PATTERN.search(line):
                    issues.append(
                        f"{doc_name}: test '{test_name}' described as 'full-shape' "
                        f"but name indicates scaled/single_tile: {line.strip()[:120]}"
                    )

    return (len(issues) == 0, issues)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check signoff docs for overclaim (scaled tests labeled full-shape)"
    )
    parser.add_argument(
        "--check-scaled-labels",
        action="store_true",
        default=True,
        help="Check that scaled/single_tile tests are not described as full-shape",
    )
    args = parser.parse_args()

    if args.check_scaled_labels:
        checklist_text = load_markdown(CHECKLIST_PATH)
        testcase_text = load_markdown(TESTCASE_LIST_PATH)

        if checklist_text is None:
            print(
                f"INFO: {CHECKLIST_PATH} not found "
                f"(will be created in T6), skipping checklist scan"
            )
        if testcase_text is None:
            print(f"WARNING: {TESTCASE_LIST_PATH} not found — nothing to check")
            sys.exit(0)

        passed, issues = check_scaled_labels(checklist_text, testcase_text)

        if not passed:
            print(f"ERROR: Found {len(issues)} overclaim(s) in signoff documentation:")
            for issue in issues:
                print(f"  - {issue}")
            sys.exit(1)
        else:
            print("OK: No scaled/single_tile tests described as full-shape")

    sys.exit(0)


if __name__ == "__main__":
    main()
