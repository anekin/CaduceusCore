#!/usr/bin/env bash
# ── check_flatc_version.sh ──────────────────────────────────────────
# Verify that the flatc CLI tool and the pip flatbuffers package share
# the same version.  Exit 0 on match, non-zero with a clear message on
# mismatch or missing tool.
#
# Expected versions: 25.2.10 (pinned across the whole repo)
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

# 1.  Locate flatc
FLATC=""
if command -v flatc &>/dev/null; then
    FLATC="flatc"
elif [ -x /usr/local/bin/flatc ]; then
    FLATC="/usr/local/bin/flatc"
elif [ -x /tmp/flatbuffers-25.2.10/build/flatc ]; then
    FLATC="/tmp/flatbuffers-25.2.10/build/flatc"
else
    echo "✗ flatc not found in PATH or known locations"
    echo "  Install it via: pip install flatbuffers==25.2.10"
    echo "  (the compiler binary ships inside the pip package since v24)"
    exit 1
fi

# 2.  Get flatc version
FLATC_VER=$("$FLATC" --version 2>&1 | head -1 | grep -oP 'version \K[0-9]+\.[0-9]+\.[0-9]+' || true)
if [ -z "$FLATC_VER" ]; then
    echo "✗ Could not parse version from '$FLATC --version'"
    "$FLATC" --version 2>&1 || true
    exit 1
fi
echo "✔ flatc --version = $FLATC_VER  ($FLATC)"

# 3.  Get pip flatbuffers version
PIP_VER=$(python3 -c "import flatbuffers; print(flatbuffers.__version__)" 2>/dev/null || true)
if [ -z "$PIP_VER" ]; then
    echo "✗ pip package 'flatbuffers' not installed or importable"
    echo "  Install it via: pip install flatbuffers==25.2.10"
    exit 1
fi
echo "✔ pip flatbuffers   = $PIP_VER"

# 4.  Compare
if [ "$FLATC_VER" != "$PIP_VER" ]; then
    echo "✗ Version mismatch!"
    echo "  flatc compiler:  $FLATC_VER"
    echo "  pip flatbuffers: $PIP_VER"
    echo ""
    echo "  Both must be pinned to the same version (25.2.10)."
    echo "  Run: pip install flatbuffers==25.2.10"
    exit 1
fi

echo "✔ flatc and pip flatbuffers are both at $FLATC_VER — OK"
