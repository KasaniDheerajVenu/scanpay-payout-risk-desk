# Testing strategy

Use independently fabricated fixtures and real disposable PostgreSQL (not SQLite) for integration tests. Unit-test pure risk functions, API integration and React components; run Ruff, Pytest, ESLint, TypeScript and Vite build. Add Playwright or equivalent smoke tests where feasible. CI uses synthetic DB service; no private records/secrets. Report only executed tests and metrics. Tests must not rely on a fixed score or force a target outcome on actual data.

| Area | Acceptance tests |
| --- | --- |
| Schema and mapping | All five exact schemas, every column preserved in typed form, leading zeros, cents, IST, nulls, enums, FK failures, no raw CSV content storage |
| Initial import | All five valid files accepted, assessment mode 24/20 checks; malformed or incomplete input rolls back all source inserts |
| Incremental import | Two-file batch plus optional teams/history; exact duplicate no-op; conflicting existing ID rejects; mixed valid/invalid rows rollback; no hardcoded filename/date |
| Historical disposition | Pending payout transitions to verified historical disposition only once, same financial fields, original prediction/history retained; conflicting update rejected |
| Privacy | Temporary files removed on success/failure; no raw data in logs/errors/API; demo rejects arbitrary multipart uploads before processing; synthetic preset only; no real DB in AI workspace |
| Reconciliation | Captured count and base+tip, decline/refund retained, inclusive IST coverage, weekend windows, incomplete history excluded, overlapping payouts caught |
| DB-only evaluation | Delete synthetic input files after import; train and infer from database; selected batch membership only; snapshot prevents concurrent imports changing inputs |
| Training eligibility | Only approved cutoff-known historical records, no pending or unverified overrides, no future/leakage or self-baselines, suspended-team history handled by eligibility |
| Statistics | Team/profession comparisons, peer excludes subject, sparse fallback, zero IQR, comparable windows, actionable evidence |
| Graph | Confirmed fraud, weak links, blank ACH, victim vs fraudster, separate clusters, stable keys, no propagation on weak edges, run-specific snapshots |
| ML | Pooled Isolation Forest fit, chronological preprocessing/validation, reproducible score, threshold/gate, missing data, diagnostic/failure status, metadata, exact reuse or refit when history changes; ML-only review if actionable, never reject |
| Run persistence | All selected payouts published atomically, failures leave prior results intact, retry idempotent, changed input/config new run, prior overrides untouched |
| Auth | Password hashing, session expiry, inactive users, forged actor ignored/rejected, role enforcement, CSRF/origin, demo scopes isolated, safe error responses |
| Override | Original and prior event rows unchanged, reason required, valid evidence, cluster required iff Reject, version 2+, stale concurrent request 409, rollback, actor from session, restart durability |
| Monitoring | Count and cent totals reconcile, effective analyst approval distinguished, 20-day IST payout-date history, historical resolved manual approvals not open reviews |
| Export | Exact six headers, one unique row per selected payout, initial 20, correct labels, cluster blank for other decisions, full batch despite filters, latest scoped override, safe escaping |
| Responsive UI | 360/768/1024/1440px, keyboard/focus/contrast, upload errors, batch/run selection, status and retry, override conflict, no page overflow or fake numbers |
| Deployment | Single container, health, migrations, synthetic Neon, demo credentials/isolated reviewers, no arbitrary files, no private artifacts in Docker context |

Private developer performs actual-data validation outside AI access and delivers CSV privately; do not claim actual results or model performance before execution. Proposed local performance targets are benchmarks, not guaranteed SLAs.
