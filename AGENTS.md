# Engineering guidelines

Before any implementation read `codex_prompt.md` (or `SCANPAY_CODEX_MASTER_PROMPT.md` if that is the local filename), README.md and all relevant docs, especially system-design.md, database-design.md, risk-engine-design.md, api-design.md and ADRs. Latest DB-first design supersedes six-table, single-batch, SQLite, anonymous-only and ML-disabled proposals. Report conflicts; preserve unrelated user work. Inspect safe Git status and existing code, implement only first incomplete requested phase, plan, test, report exact staging commands and Conventional Commit suggestion, then stop. Never stage/commit/push/merge/deploy or change branch without explicit approval. No fabricated test metrics/history.

## Privacy

Never access actual CSVs, private database, real rows, derived outputs, secrets, actual notes or model artifacts. Only documented schemas, source code and independently fabricated fixtures. Developer runs private local application separately outside agent access. Do not read private paths or env values, or process actual files. `.gitignore` is not access control. Public demo only synthetic presets; reject arbitrary uploads server-side. No raw file bytes in PostgreSQL, no real data to external inference, AI tools, Git or hosting. Use safe configuration examples and separate DB credentials.

## Architecture and correctness

Monorepo, FastAPI/Pydantic/SQLAlchemy/Alembic/PostgreSQL, React/TypeScript/Vite/Tailwind/shadcn/ui, same-origin Render/Neon demo. Keep routes thin, services orchestrate, repositories query DB, domain functions pure. Implement eleven-table design with all CSV source fields typed; temporary files removed; metadata only. Import initial and incremental batches atomically, dedup exact IDs, reject conflicts except documented verified disposition completion. Evaluation reads PostgreSQL snapshot, no CSV reread; store model metadata, per-payout ML findings and immutable decision per run. Use trusted cutoff-known approved history, no pending/fraud/unverified override learning. Required Isolation Forest train/validate/infer with explicit fallback; no ML-only Reject. Known fraud requires established evidence and cluster. Captured count/amount+tip, integer cents, IST inclusive dates, retain other attempts. No hardcoded IDs/decisions/metrics. Authenticated actor from session, roles server-side, append-only versions and derived current state, no overwriting originals. Same scoped projection for dashboard/detail/export. Preserve prior runs.

## UI and quality

Use central theme tokens, accessible consistent components, responsive 360/768/1024/1440px, real API data, meaningful loading/empty/error states, upload/batch/run workflows and audit timeline. Keep docs and tests updated. Verify on synthetic PostgreSQL; run lint, types, tests and build. Report blockers and actual checks. Create only necessary files; do not add speculative abstractions or unrelated features.
