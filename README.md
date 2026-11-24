# ScanPay Payout Risk Desk

Payout Risk Desk is a take-home assessment project for reviewing pending payouts. The planned dashboard explains each decision, shows its supporting records, preserves analyst overrides, compares counts and USD exposure against 20 days of history, and exports the complete `decisions.csv`.

## Project status

The architecture and API contracts are documented. The backend, frontend, database migration, and risk pipeline are still to be implemented. The repository has no application startup or test commands yet.

## Architecture

- The React and TypeScript frontend uses a shared, accessible component system and a responsive layout. FastAPI serves the built frontend and same-origin REST API in one Render container. The hosted demo uses Neon PostgreSQL and signed reviewer scopes for isolated overrides.
- The local batch command will reconcile payout coverage and amounts, compare activity with team and profession baselines, build a NetworkX graph of known fraud, and run the required Isolation Forest pipeline before publishing decisions to PostgreSQL.
- Known fraud exposure can lead to Reject. Statistical anomalies and validated, evidence-backed ML findings can lead to Manual review. Isolation Forest attempts training, chronological validation, and eligible-payout inference during normal batch evaluation. Unreliable results produce a documented fallback.
- The six PostgreSQL tables preserve source data, known cases, immutable original decisions, and append-only overrides. The dashboard and CSV use the latest effective decision for the reviewer’s scope.

## Design references

- [System design](docs/system-design.md)
- [Database design](docs/database-design.md)
- [Risk engine design](docs/risk-engine-design.md)
- [Isolation Forest decision record](docs/decisions/004-isolation-forest.md)
- [Engineering guidelines](AGENTS.md)

## Data handling

Development, automated tests, and the hosted demo use independently fabricated synthetic records. The developer runs the actual assessment in a separate private workspace and database. Keep actual source files, reasons, outputs, model artifacts, and credentials out of Git, AI tools, and the hosted environment. Deliver the real `decisions.csv` privately.
