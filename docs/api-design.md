# Same-origin REST API design

Use `/api` routes, typed Pydantic and TypeScript contracts. All domain reads require authentication except health; roles checked server-side. Never return raw sensitive columns or original CSV bytes. Resolve review scope from authenticated user and environment, not request body. Common synthetic source and seeded results are read-only to reviewers; their events are account-scoped. All local authorized analysts share the local scope.

| Method and route | Permission | Contract |
| --- | --- | --- |
| POST /api/auth/login | public, rate limited | username/password; signed HttpOnly session cookie, safe user metadata |
| POST /api/auth/logout | authenticated | clear cookie; documented expiry/revocation semantics |
| GET /api/auth/me | authenticated | user id/name/role, mode and permissions |
| POST /api/datasets/upload | admin, private only | multipart mode, cutoff, typed file fields: teams, history_transactions, history_payouts, latest_transactions, pending_payouts; return batch ID, status, counts and sanitized validation |
| POST /api/datasets/demo-import | demo admin only | allowlisted synthetic preset ID, no file or path input |
| GET /api/datasets | authorized reader | list batch IDs, cutoff, status, counts, timestamps |
| GET /api/datasets/{id} | authorized reader | file metadata/validation, no contents |
| POST /api/evaluations | admin | batch_id; explicit cutoff from batch, optional documented config version; create/reuse run, return run ID/status |
| GET /api/evaluations | authorized reader | filter batch_id, list run status/date/model version |
| GET /api/evaluations/{id} | authorized reader | run status, summary, cutoff and safe model metadata |
| GET /api/models | authorized reader | model versions, gate metrics, counts, fit window |
| GET /api/dashboard?run_id=... | authorized reader | total and four categories counts/cents, historical daily series and comparison window, ML summary |
| GET /api/payouts?run_id=... | authorized reader | queue for selected completed run, search/filter/sort optional |
| GET /api/payouts/{id}?run_id=... | authorized reader | original/effective, coverage, safe attempts, evidence, both baselines, model result, audit timeline |
| POST /api/payouts/{id}/overrides | analyst/admin | run_id, decision, reason, expected_version, typed evidence, cluster_id iff Reject; 201 with new effective state |
| GET /api/payouts/{id}/history?run_id=... | authorized reader | version 1 and scoped append-only events |
| GET /api/fraud-clusters?run_id=... | authorized reader | known clusters and safe supporting IDs/paths |
| GET /api/exports/decisions.csv?run_id=... | analyst/admin | full run effective CSV, attachment |
| GET /health | public | safe liveness/readiness, no credentials or private stats |

Initial import requires all five fields; incremental requires latest_transactions + pending_payouts, optional teams/history files. Filenames do not determine schemas. Support verified historical dispositions via history_payouts, with optional history_transactions for newly imported history. Limit file count, total bytes, rows and field length; no arbitrary local paths or ZIPs. In demo reject arbitrary uploads before reading content; built-in synthetic presets only. Do not log multipart content. HTTP 201 for accepted import/new synchronous completed run/override; 202 for persisted asynchronous evaluation if actually implemented; 200 for idempotent existing result; 401 unauthenticated, 403 forbidden, 404 unavailable scope or ID, 409 stale version/conflicting IDs/concurrent operation, 413 oversized, 422 invalid schema/reconciliation/decision, 503 unavailable. A failure records sanitized status separately. Never return partial results as completed.

## Override example (fictional)

```json
{"run_id":"DEMO-RUN-ID","decision":"APPROVE","reason":"Verified the payment against the invoice.","expected_version":1,"evidence":[{"type":"transaction","id":"DEMO-TX-1"}],"cluster_id":null}
```

Actor is derived from authenticated session, never submitted. Lock original decision and append next version in one transaction. Reject requires known cluster in this run and supported exposure; review requires supporting evidence. For non-Reject cluster is null. Use exact enum values internally. Response contains original/effective decision, decision_source, effective_version, actor, history event ID and timestamp. History exposes CURRENT/SUPERSEDED derived in selected scope. Run selection mandatory for all result APIs; older runs accessible and no automatic cross-run override carryover. Use no-store for personalized responses. Signed cookie + same-origin/CSRF checks on writes; verify account active/auth_version on requests, use Argon2id, secure cookies in production, short expiry and no token in localStorage. No verified identity claim for any shared demo credential; prefer unique reviewer accounts.

## Export and errors

Export exact six columns and case-sensitive CSV labels from one effective-state snapshot, not client filters. Initial assessment 20 unique IDs; later count equals selected run membership. Escape CSV using standard library; prevent spreadsheet formula injection in free-text export without altering required IDs or decision labels (validate unsafe source ID forms at import). Reasons actionable, evidence semicolon-separated typed source IDs rendered as IDs. Do not leak sensitive values. Return consistent safe error code/message and row number/field where helpful; no raw input or stack traces. Historical and current metrics use same run cutoff and snapshot; expose history date range and denominator. No real payout operations.
