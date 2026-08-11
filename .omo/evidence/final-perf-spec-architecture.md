{
  "command": "audit",
  "utc": "2026-08-11T11:05:56.457600+00:00",
  "head": "180c2b945536b1181f8189e80448d934ab9d7ef1",
  "checks_requested": [
    "event-source",
    "numerical-separation",
    "oracle-independence",
    "no-rtl",
    "typed-errors"
  ],
  "results": {
    "event-source": {
      "check": "event-source",
      "status": "ok",
      "verdict": "pass",
      "detail": {
        "rtl_refs_found": 0,
        "files": []
      }
    },
    "numerical-separation": {
      "check": "numerical-separation",
      "status": "ok",
      "verdict": "pass",
      "detail": {
        "verifier_ast_clean": true,
        "reducer_ast_clean": true
      }
    },
    "oracle-independence": {
      "check": "oracle-independence",
      "status": "ok",
      "verdict": "pass",
      "detail": {
        "verifier_ok": true,
        "reducer_ok": true,
        "violations": []
      }
    },
    "no-rtl": {
      "check": "no-rtl",
      "status": "ok",
      "verdict": "pass",
      "detail": {
        "rtl_refs_found": 8,
        "files": [
          ".omo/evidence/task-1-soc-phase3-4.txt",
          ".omo/evidence/task-16-soc-phase3-4.txt",
          ".omo/evidence/task-3-soc-phase3-4.txt",
          ".omo/evidence/task-D-soc-phase3-4.txt",
          ".omo/evidence/task-9-p2p3-full-rtl.txt",
          ".omo/evidence/task-22-release-signoff.json",
          ".omo/evidence/task-22-release-signoff-rerun.json",
          ".omo/evidence/task-2-binding-migration.log"
        ]
      }
    },
    "typed-errors": {
      "check": "typed-errors",
      "status": "ok",
      "verdict": "pass",
      "detail": "typed-errors: provider gates use typed error types"
    }
  }
}
