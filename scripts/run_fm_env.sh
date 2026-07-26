#!/bin/bash
#
# Env-var wrapper for direct pytest invocations under Func Model signoff.
# Sets PYTHONPATH=sim and QWEN3B_GGUF (default ~/models/qwen2.5-3b-instruct-q4_k_m.gguf,
# overridable via existing env), then execs "$@".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

_venv_deps="${REPO_ROOT}/.venv_deps"
if [ -d "$_venv_deps" ]; then
    export PYTHONPATH="${_venv_deps}:${REPO_ROOT}/sim${PYTHONPATH:+:$PYTHONPATH}"
else
    export PYTHONPATH="${REPO_ROOT}/sim${PYTHONPATH:+:$PYTHONPATH}"
fi
unset _venv_deps
export QWEN3B_GGUF="${QWEN3B_GGUF:-/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf}"

# Allow caller to override Python interpreter via FM_PYTHON env var.
# When FM_PYTHON is unset, behavior is identical to bare "python3".
_use_python="${FM_PYTHON:-}"
if [ -n "$_use_python" ]; then
    _args=()
    for _arg in "$@"; do
        if [ "$_arg" = "python3" ]; then
            _args+=("$_use_python")
        else
            _args+=("$_arg")
        fi
    done
    set -- "${_args[@]}"
fi
unset _use_python _args _arg

exec "$@"
