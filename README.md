# Payout Risk Desk

A risk operations application for evaluating merchant payouts against historical behaviour and known fraud cases. The system is designed to support explainable decisions, analyst investigation, audited overrides and financial risk monitoring.

> Project status: implementation in progress. The development section lists the workflows currently available.

## Project goals

- Approve payouts with normal behaviour and no established fraud exposure.
- Route unusual or uncertain activity to manual review.
- Reject automatically only when evidence establishes exposure to known fraud.
- Provide traceable reasons, supporting records and fraud cluster identifiers.
- Preserve original decisions and analyst overrides.
- Compare current payout activity with historical patterns and export `decisions.csv`.

## Architecture and technology

The project uses a monorepo with a React/TypeScript frontend and a modular FastAPI backend. Planned components include Pandas and NumPy for analysis, NetworkX for fraud relationships, robust statistical baselines, and a required Isolation Forest pipeline with explicit quality-gated fallback. SQLAlchemy and Alembic support PostgreSQL locally and Neon PostgreSQL for hosted persistence. The application does not execute money transfers.

## Repository layout

```text
.
├── backend/          # Planned: FastAPI, risk engine, persistence, tests
├── frontend/         # Planned: React dashboard and tests
├── sample_data/      # Synthetic demo presets
├── docs/             # Requirements, architecture and API design
├── AGENTS.md         # Coding-agent and privacy guidelines
├── .gitignore
├── .editorconfig
└── README.md
```

## Design documentation

| Document | Purpose |
| --- | --- |
| [Requirements](docs/requirements.md) | Scope, priorities and acceptance criteria |
| [Business rules](docs/business-rules.md) | Settlement, decision and override policies |
| [System design](docs/system-design.md) | Architecture, workflows, deployment and UI |
| [Risk engine](docs/risk-engine-design.md) | Features, baselines, fraud and ML |
| [Database design](docs/database-design.md) | Tables, constraints and relationships |
| [API design](docs/api-design.md) | Endpoints, contracts and access control |
| [Testing strategy](docs/testing-strategy.md) | Business and integration test plan |
| [Architecture decisions](docs/decisions/README.md) | Technical choices and trade-offs |

## Data privacy

The assessment datasets are processed locally and must not be committed, uploaded to AI coding tools, or included in public deployments. Public demos use independently generated synthetic data. Private outputs, databases, logs and trained model artifacts remain outside the repository. Any different arrangement requires explicit authorization from the data owner.

## Development and deployment

The frontend provides a local status screen. The backend supports authenticated dataset import, evaluation through the API or private CLI, dashboard and investigation reads, analyst overrides and CSV export. The responsive workflow UI is planned for later phases.

### Verify the PostgreSQL schema locally

From PowerShell in the repository root, start Docker Desktop and create an empty synthetic database on a dedicated loopback port. The password below is for this disposable local database.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
docker run --name scanpay-phase2-test --rm -d -p 127.0.0.1:55432:5432 -e POSTGRES_USER=scanpay_test -e POSTGRES_PASSWORD=scanpay-test-local -e POSTGRES_DB=scanpay_test postgres:18
docker exec scanpay-phase2-test pg_isready -U scanpay_test -d scanpay_test
$env:SCANPAY_TEST_DATABASE_URL = 'postgresql+psycopg://scanpay_test:scanpay-test-local@127.0.0.1:55432/scanpay_test'
.\.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml backend/tests
.\.venv\Scripts\python.exe -m ruff check backend
```

Wait until `pg_isready` reports that PostgreSQL is accepting connections before running the tests. The integration suite starts from an empty `scanpay_test` database, upgrades it with Alembic, tests downgrade and upgrade, then returns it to the base revision. Run the suite before leaving this database migrated for local app use.

```powershell
$env:SCANPAY_DATABASE_URL = $env:SCANPAY_TEST_DATABASE_URL
$env:SCANPAY_MIGRATION_DATABASE_URL = $env:SCANPAY_TEST_DATABASE_URL
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini check
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

`GET http://127.0.0.1:8000/health` keeps `status: "ok"` for process liveness. A migrated database reports `database.connectivity: "connected"` and `database.ready: true`. `evaluation_data` remains `"not_ready"` in Phase 2. The frontend still uses the relative `/health` URL through the Vite development proxy. Use `npm.cmd --prefix frontend ci` and `npm.cmd --prefix frontend run dev` in a second terminal if needed.

For configuration via an environment file, copy `backend/.env.example` to `backend/.env`, update it with your dedicated synthetic connection string, and pass `--env-file backend/.env` when starting Uvicorn. Alembic reads process environment variables and prefers the direct migration URL. Use synthetic credentials in development and keep actual assessment processing in the separate private environment.

When finished, stop the disposable Docker database with `docker stop scanpay-phase2-test`.

### Verify authentication with a synthetic user

Leave the synthetic database running and migrated. From the repository root, set the signing secret for your current PowerShell session and run the provisioning command. Enter a new synthetic password at the hidden prompt. Provision each reviewer separately using `ADMIN`, `ANALYST`, or `VIEWER` as appropriate.

```powershell
$env:SCANPAY_APP_MODE = 'local'
$env:SCANPAY_DATABASE_URL = $env:SCANPAY_TEST_DATABASE_URL
$env:SCANPAY_AUTH_SIGNING_SECRET = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
Push-Location backend
try {
    ..\.venv\Scripts\python.exe -m scripts.create_user --username synthetic-analyst --display-name 'Synthetic Analyst' --role ANALYST
} finally { Pop-Location }
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

With Vite running on port 5173, run the following in the browser console on `http://127.0.0.1:5173`, or use an API client with a cookie jar and the same `Origin` header. Supply the password you entered when provisioning the synthetic account.

```javascript
const getCsrf = async () => (await (await fetch('/api/auth/csrf')).json()).csrf_token
const password = prompt('Synthetic account password')
const login = await fetch('/api/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': await getCsrf() },
  body: JSON.stringify({ username: 'synthetic-analyst', password }),
})
console.log(login.status, await (await fetch('/api/auth/me')).json())
console.log((await fetch('/api/auth/logout', {
  method: 'POST', headers: { 'X-CSRF-Token': await getCsrf() },
})).status)
```

Expect 200 for login and `/me`, then 204 for logout and 401 for a subsequent `/me`. Logout revokes all sessions for the signed-in account. The [API design](docs/api-design.md#authentication-and-frontend-integration) covers cookie handling, frontend error handling, and rate-limit scope. Use `backend/.env.example` for configuration names. Auth routes require a signing secret. Demo mode also requires a database URL, Secure cookies and explicit HTTPS trusted origins.

Use small, reviewable changes and Conventional Commits. See [AGENTS.md](AGENTS.md) for project conventions.

### Import an independently fabricated dataset

Start with a migrated disposable local PostgreSQL database. Set `SCANPAY_DATABASE_URL`, `SCANPAY_AUTH_SIGNING_SECRET`, and `SCANPAY_APP_MODE=local` in PowerShell. Provision a local application admin using the earlier instructions, then run the CLI from the repository root.

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
$sample = 'sample_data/synthetic-v1'
.\.venv\Scripts\python.exe -m scripts.import_dataset `
    --username synthetic-admin --mode INITIAL --cutoff '2025-02-20T10:00:00+05:30' `
    --teams "$sample/teams.csv" --history-transactions "$sample/history_transactions.csv" `
    --history-payouts "$sample/history_payouts.csv" --latest-transactions "$sample/latest_transactions.csv" `
    --pending-payouts "$sample/pending_payouts.csv"
```

Enter the local admin's application password at the hidden prompt. The command prints the batch ID and status. Repeating it returns the same ID with `"created": false`. Incremental imports use `--mode INCREMENTAL` with an explicit cutoff, `--latest-transactions`, and `--pending-payouts`. The other typed files are optional after an initial import. Use `--assessment` for the original 24-team and 20-payout count check. The synthetic preset uses general mode.

The hosted API accepts the bundled `synthetic-v1` preset. Package `sample_data/synthetic-v1` alongside the application when wiring deployment. [Dataset API contracts](docs/api-design.md#dataset-requests-and-responses) cover multipart fields, CSRF, responses, and failure behavior.

## Database-first workflow

After importing a local dataset, evaluate its explicit batch ID with the same database and local admin account used for import. The CLI prompts for the password and returns the run ID, status, and whether a new result was created.

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m scripts.evaluate_batch --username local-admin --batch-id YOUR_BATCH_UUID
```

Equivalent completed requests reuse the run. While an equivalent attempt is running, the command returns a conflict. A retry starts a fresh attempt after failure or after an unpublished attempt expires. See [evaluation lifecycle](docs/database-design.md#evaluation-input-boundary) for the retry policy.
