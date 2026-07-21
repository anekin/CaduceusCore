#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"
case "${1-}" in --help|-h) echo "Usage: p9_log_bug.sh [--id ID --type <fw|rtl|integ> --symptom TXT --root_cause TXT --evidence PATH --verdict <resolved|open|rtl-suspect>] | [--rtl-report SLUG ...]"; echo "Options include: --id, --type, --symptom, --root_cause, --evidence, --verdict, --rtl-report"; exit 0 ;; esac; echo "[p9_log_bug] placeholder -- parses args and appends docs/bugs/bugs-soc-rtl.md"
