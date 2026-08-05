#!/usr/bin/env python3
"""Auto-prepend Caduceus repo-root subdirectories to ``sys.path``.

This helper lets signoff runners be invoked without manually exporting
``PYTHONPATH=sim:gen:software``.  It is imported by every top-level runner
after a minimal inline bootstrap guarantees ``sim/`` is importable.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_repo_pythonpath(repo_root: Path | str) -> None:
    """Prepend Caduceus repo-root paths to ``sys.path`` if absent.

    Inserts *repo_root* itself (so ``gen`` and ``software`` packages are
    importable) and ``repo_root/sim`` (so the ``signoff`` package is
    importable).  Existing ``sys.path`` entries are preserved; only missing
    paths are added.  ``sim`` ends up closest to the front, matching the
    conventional ``PYTHONPATH=sim:gen:software`` precedence.
    """
    root = Path(repo_root)
    for p in (str(root / "sim"), str(root)):
        if p not in sys.path:
            sys.path.insert(0, p)
