{
  "command": "audit",
  "utc": "2026-08-11T11:04:20.330043+00:00",
  "head": "180c2b945536b1181f8189e80448d934ab9d7ef1",
  "checks_requested": [
    "scope",
    "provenance",
    "uncertainty",
    "report-only",
    "dirty-worktree"
  ],
  "results": {
    "scope": {
      "check": "scope",
      "status": "ok",
      "verdict": "pass",
      "detail": {
        "scope": "plan-level audit, no run payload"
      }
    },
    "provenance": {
      "check": "provenance",
      "status": "ok",
      "verdict": "pass",
      "detail": "provenance: HEAD=180c2b945536, dirty_paths=37"
    },
    "uncertainty": {
      "check": "uncertainty",
      "status": "ok",
      "verdict": "pass",
      "detail": "uncertainty: low/base/high bands present in report-only KPIs"
    },
    "report-only": {
      "check": "report-only",
      "status": "ok",
      "verdict": "pass",
      "detail": "report-only: scaling and KPI reports marked report_only=true"
    },
    "dirty-worktree": {
      "check": "dirty-worktree",
      "status": "ok",
      "verdict": "pass",
      "detail": {
        "total_dirty": 37,
        "omo_allowlisted": 37,
        "non_omo_dirty": []
      }
    }
  },
  "zero_waivers": true
}
