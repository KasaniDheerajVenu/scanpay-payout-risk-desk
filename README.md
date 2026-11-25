# ScanPay Payout Risk Desk

React and FastAPI application for reviewing merchant payouts, recording analyst decisions, and exporting decisions.csv. It uses PostgreSQL and Alembic, team and profession baselines, NetworkX fraud evidence, and Isolation Forest with validation gates.

## Run locally on Windows

From PowerShell, create a virtual environment and install the backend dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
```

Create a PostgreSQL database for your local application. Set SCANPAY_DATABASE_URL and SCANPAY_MIGRATION_DATABASE_URL in the current shell using that database's connection details. Generate a fresh signing secret for the local session, migrate the database, and provision an admin:

```powershell
$env:SCANPAY_APP_MODE = 'local'
$env:SCANPAY_AUTH_SIGNING_SECRET = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
$env:PYTHONPATH = (Resolve-Path backend).Path
.\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m scripts.create_user --username scanpay_admin --display-name "ScanPay Administrator" --role ADMIN
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

In a second terminal, install the frontend dependencies and start Vite:

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev
```

Open http://127.0.0.1:5173/#/login. In local mode an admin can import the five CSVs under **Datasets**, then run an evaluation on the Ready batch. Select a completed run to review payouts and export decisions.

## Hosted demo

Use synthetic data only. Set SCANPAY_APP_MODE=demo on the backend. An optional approved synthetic preset can be supplied outside Git by setting SCANPAY_DEMO_PRESET_DIR to a server-side directory containing teams.csv, history_transactions.csv, history_payouts.csv, latest_transactions.csv, and pending_payouts.csv in the existing synthetic-v1 format. Without those files, the preset import returns 503. Previously imported datasets remain available.

See backend/.env.example and frontend/.env.example for settings. For separate frontend and backend origins, set VITE_API_URL at frontend build time, enable SCANPAY_CORS_ENABLED, and allowlist the frontend's exact HTTPS origin in SCANPAY_TRUSTED_ORIGINS. Cross-site authentication also requires Secure cookies and SameSite=None and can be limited by browser cookie settings.

## Regression tests

The CSVs under backend/tests/fixtures are synthetic regression inputs. Run backend integration tests only against an empty disposable PostgreSQL database named scanpay_test or scanpay_test_<suffix>, using SCANPAY_TEST_DATABASE_URL with an explicit loopback port. The tests migrate and downgrade that database.

```powershell
.\.venv\Scripts\python.exe -m pytest -c backend/pyproject.toml backend/tests
.\.venv\Scripts\python.exe -m ruff check backend
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run test
npm.cmd --prefix frontend run build
```

Keep actual assessment files, database credentials, generated decisions and model artifacts outside Git.
