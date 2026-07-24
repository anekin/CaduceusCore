#!/bin/bash
#
# Env-var wrapper for direct pytest invocations under Func Model signoff.
# Sets PYTHONPATH=sim and QWEN3B_GGUF (default ~/models/qwen2.5-3b-instruct-q4_k_m.gguf,
# overridable via existing env), then execs "$@".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export PYTHONPATH="${REPO_ROOT}/sim${PYTHONPATH:+:$PYTHONPATH}"
export QWEN3B_GGUF="${QWEN3B_GGUF:-/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf}"

exec "$@"
