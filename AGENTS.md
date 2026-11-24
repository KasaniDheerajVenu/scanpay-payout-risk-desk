# Engineering guidelines

## Scope and workflow

- Read relevant documents under `docs/` before implementing a feature. Latest system-design.md, database-design.md, risk-engine-design.md and master prompt supersede older proposed designs; reconcile conflicting documentation before implementation. If a design assumption changes, update the affected documentation in the same change or a follow-up commit.
- Implement only the requested feature. Prefer simple, working modules over speculative abstractions.
- Do not claim a feature, test, model metric or deployment is complete unless verified.
- Never commit, push, merge, rewrite history or deploy without explicit developer approval. Show changes and test results for review first.
- Use Conventional Commits: `type(scope): imperative description`. Keep each commit logically coherent; include relevant tests.

## Privacy and AI access

- The actual assessment data, including historical reasons, must not be read, searched, printed, uploaded or otherwise processed by an AI coding agent. Only schemas, developer-written code and independently fabricated test fixtures may be shared.
- Develop and test in an AI-accessible workspace containing only synthetic data. The developer runs actual-data processing in a separate local environment inaccessible to the agent. Do not follow links or paths into the private workspace.
- Do not access private databases, actual exports, private model artifacts, logs, environment secrets or terminal output containing real records.
- Never commit real datasets, actual decisions, credentials or artifacts derived from private data. `.gitignore` is not an access-control boundary.
- The hosted application uses synthetic data only. Disable real-data ingestion and unrestricted evaluation in demo mode. Do not use external LLM or inference APIs.

## Architecture

- Use one repository with independent `frontend/` and `backend/` directories.
- Backend: FastAPI, Pydantic, SQLAlchemy and Alembic. Keep HTTP routes thin, orchestration in services, domain logic in the risk engine, and database access in repositories.
- Frontend: React, TypeScript, Vite, Tailwind CSS and typed API clients. Use accessible shadcn/ui or equivalent components, shared theme tokens, responsive layouts and clear loading, empty and error states. Verify at 360, 768, 1024 and 1440 CSS px.
- Use PostgreSQL locally and Neon PostgreSQL for synthetic hosted state. Maintain the six tables in the latest database design. Use one Render container serving React and FastAPI with same-origin APIs, and server-signed review scopes instead of JWT/RBAC. Avoid framework-specific code inside core risk algorithms.
- Use integer cents for money, typed identifiers and timezone-aware timestamps. Preserve leading zeros in identifiers.

## Risk policy

- Exactly one automated and one effective decision per evaluated pending payout; automated decisions are preserved.
- Reject only for established exposure to a previously confirmed fraud case. An anomaly or ML score alone can never cause rejection.
- Manual review handles meaningful anomalies, unresolved suspicious links and material uncertainty. Approval requires adequate evidence of normal activity.
- All Manual review and Reject decisions need actionable reasons and actual supporting IDs. Reject requires a valid fraud cluster. Ignore blank identifiers; do not treat shared weak attributes as proof.
- Use only captured payments to reconcile payout totals; retain other attempts for risk analysis. Use IST collection dates and comparable historical windows.
- Exclude confirmed fraudulent observations from normal baselines. Do not leak future information into historical validation or training features.
- Isolation Forest implementation, training, validation, eligible-payout inference and reporting are mandatory. Attempt ML in normal batch evaluation; record an explicit fallback if unreliable. It may affect Manual review only after validation and actionable explanation. Never describe its score as a fraud probability or allow it to cause Reject.
- Preserve original findings and append override events atomically; require a reason and enforce review scope, evidence, cluster and version checks server-side.

## Code quality and testing

- Prefer type hints, descriptive names, small pure functions and explicit failure handling. Avoid unnecessary dependencies.
- Validate API requests, use parameterized queries/ORM and do not expose sensitive fields or stack traces.
- Use deterministic synthetic fixtures and test boundary conditions, reconciliation, fraud evidence, audit consistency and export completeness.
- Run applicable tests, linting and type checks before suggesting a commit. Do not modify unrelated files or disable tests to make a build pass.
- Document commands actually supported by the repository, configuration defaults, assumptions and known limitations.
