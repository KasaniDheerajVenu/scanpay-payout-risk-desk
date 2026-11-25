# Requirements specification

Status: proposed design, to be verified during implementation. **Required** denotes the assessment brief or the updated submission instructions reported by the candidate. **Design** denotes an engineering choice. **Optional** denotes an enhancement.

## 1. Problem and actors

ScanPay collects payments for field-service businesses (teams). Before a payout leaves ScanPay, risk operations needs a decision backed by history and evidence. The supplied scenario is 13 November 2026 at 10:00 IST; 20 pending payouts created on 12 November cover 10 November collections. History includes 24 teams and the preceding 20 days of payment activity. The application is decision support, not a payment processor.

Actors: risk analyst (investigate and override); viewer (read-only demo access); admin (configuration, evaluation and user provisioning). Roles are a proposed extension, not required in the original brief.

## 2. Scope

Required: ingest supplied data locally, evaluate all pending payouts, learn team and profession norms, recognize known fraud, provide reasons and evidence, support overrides, monitor amounts and counts against history, export decisions, and provide repository and deployment links under the updated instructions.

Design: full-stack React/FastAPI application, durable audit storage, synthetic deployed demo, configurable statistical thresholds, graph-based fraud linkage, basic authenticated access for hosted operations.

Design (required for this project): Isolation Forest training/validation/inference, CSV upload, database-first evaluation, multi-batch persistence and authenticated audit. Optional: dedicated graph visualization, advanced user management, configurable threshold UI.

Out of scope: real payment processing, bank transfers, settlement scheduling, streaming, microservices, LLM/RAG, real-time model retraining, and hosting actual assessment data without explicit approval.

## 3. Functional requirements

| ID | Requirement | Priority | Origin |
| --- | --- | --- | --- |
| FR-01 | Load all five CSV files and validate headers, IDs, types, foreign keys and dates | P0 | Design |
| FR-02 | Associate transactions to payouts using team and inclusive IST coverage dates; reconcile captured count and amount including tips | P0 | Required rules / design validation |
| FR-03 | Retain declined and refunded attempts for risk analysis | P0 | Design |
| FR-04 | Compute historical and current behavioural features | P0 | Required |
| FR-05 | Learn comparable normal behaviour per team and profession | P0 | Required |
| FR-06 | Handle insufficient history, missing values and multi-day coverage; exclude confirmed fraud from normal baselines | P0 | Design |
| FR-07 | Identify confirmed historical fraud cases and build evidence-supported fraud clusters | P0 | Required |
| FR-08 | Check pending payouts for exposure to known fraud; distinguish weak links and victims | P0 | Required / design |
| FR-09 | Detect unusual activity using robust statistical comparisons | P0 | Required outcome; design method |
| FR-10 | Implement, train, validate and score eligible payouts using Isolation Forest; explicit fallback | P0 | Design |
| FR-11 | Produce exactly one decision per pending payout: Approve, Manual review or Reject | P0 | Required |
| FR-12 | Generate actionable reasons and supporting IDs for all review/reject decisions; include known cluster on Reject | P0 | Required |
| FR-13 | List all payouts with decision, amount, team and reason; provide evidence drill-down | P0 | Required |
| FR-14 | Search, sort and filter the payout queue | P1 | Design |
| FR-15 | Allow override of any original decision with a mandatory reason | P0 | Required |
| FR-16 | Persist original and effective decision, all override events and timestamps | P0 | Required / design |
| FR-17 | Monitor payout counts and dollars by decision and compare with history | P0 | Required |
| FR-18 | Show fraud clusters, related records and team/profession baselines | P1 | Design |
| FR-19 | Export all pending payouts using the required six-column decisions.csv schema | P0 | Required |
| FR-20 | Retain verified analyst feedback for future batches without silently rewriting history | P0 | Required / design |
| FR-21 | Authenticate users and enforce role-based permissions on protected API operations | P0 | Design; original brief excludes login/roles |
| FR-22 | Expose health and dataset readiness information | P1 | Design |
| FR-23 | Publish reproducible code and a working synthetic-data deployment | P0 | Updated submission instructions |
| FR-24 | Support versioned evaluation and prevent silent loss of overrides on reruns | P1 | Design |

## 4. Non-functional requirements

| ID | Requirement | Verification |
| --- | --- | --- |
| NFR-01 | Monetary precision to the cent | Reconciliation tests using integer cents |
| NFR-02 | Reproducible automated outcomes for the same input/configuration | Repeat evaluation tests; fixed model seed |
| NFR-03 | Explainability and traceability | Verify findings against existing transaction, payout or team IDs |
| NFR-04 | Atomic override and audit persistence | Transaction rollback and concurrency tests |
| NFR-05 | Durable local state | Restart test |
| NFR-06 | Data privacy | Review Git, logs, artifacts and deployment seed data; no actual rows to AI tools |
| NFR-07 | API security | Validate inputs; server-side RBAC; HTTPS and secure session handling |
| NFR-08 | Maintainability | Modular domain/services/API/repository separation, typed contracts and automated tests |
| NFR-09 | Usability | Responsive interface at 360/768/1024/1440px, clear decision labels, evidence drill-down, loading/error states |
| NFR-10 | Performance | Proposed target: batch evaluation under 5 s, precomputed API requests under 500 ms locally; benchmark and report actual results |
| NFR-11 | Observability | Health/readiness checks and structured non-sensitive logs |
| NFR-12 | Safe failure | Invalid input blocks evaluation or is explicitly held; never silently approved |
| NFR-13 | Reproducible deployment | Dependency lock/pin, environment example, tested setup, synthetic seed |

## 5. Acceptance criteria

- The actual batch contains exactly 20 distinct pending payout IDs; each appears once in the local export.
- Reject decisions have a traceable connection to a known fraud cluster. An anomaly alone cannot reject.
- Review/reject reasons are actionable and cite existing records. Team and profession comparisons are available where history permits.
- Counts and amounts across effective decision categories sum to the batch total. Historical manually approved payouts are labelled accurately.
- Overrides require a reason, preserve originals, append audit events and update monitoring/export consistently.
- The export uses `payout_id,team_id,decision,fraud_cluster,reasons,evidence`, with the prescribed decision strings and blank clusters for non-rejects.
- Synthetic demo demonstrates normal, review and known-fraud cases without exposing confidential data. Actual outputs are delivered privately.

## 6. Open questions and assumptions

- Updated deployment requirement was reported by the candidate; confirm whether synthetic demo plus local actual-data output satisfies the reviewers. Original brief says deployment and login are not needed.
- Confirm what constitutes sufficient fraud exposure and how historical reasons identify case boundaries. No exact linkage rules are assumed verified before local analysis.
- Confirm whether CSV should reflect initial or effective decisions. Proposed default: effective decisions, preserving originals internally.
- Confirm how a human rejection for newly discovered fraud without an existing cluster should be recorded. Until clarified, Reject overrides require a valid known cluster and supporting evidence; new unconfirmed cases remain reviewable.
- Thresholds, minimum history and ML usefulness are provisional until chronological local analysis. Do not publish actual findings or confidential derived artifacts without permission.

## 7. Added database-first requirements

| ID | Requirement | Priority | Origin |
| --- | --- | --- | --- |
| FR-25 | Map every supplied CSV field into typed relational columns; no raw file storage | P0 | User design |
| FR-26 | Initial five-file upload and incremental latest data upload in private app | P0 | User design |
| FR-27 | Validate and reconcile atomically, deduplicate identical rows, reject conflicting IDs, allow verified once-only disposition import | P0 | User design |
| FR-28 | Persist batch and file metadata only, including checksums and membership IDs | P0 | Design |
| FR-29 | Train and infer from eligible as-of DB snapshot, no CSV reads during evaluation | P0 | User design |
| FR-30 | Persist run and model metadata, ML score/flag, evidence and hybrid decision | P0 | User design |
| FR-31 | Select historical completed runs, preserve original automated results across reevaluation | P0 | User design |
| FR-32 | Append decision history with authenticated actor, reason, version; derive CURRENT/SUPERSEDED | P0 | User design |
| FR-33 | Show import/evaluation status, validation errors, model limitations and retry state | P0 | Design |
| FR-34 | Restrict public deployment to synthetic preset import, no arbitrary CSV upload | P0 | Privacy design |

Additional acceptance: remove input files after a successful synthetic import, then train/score/export from PostgreSQL. A second batch must learn from eligible prior verified dispositions; pending/override-only records cannot enter training. Exact duplicate imports do not duplicate source rows. Conflicting imports roll back. Previous runs and all overrides remain intact. No file content in DB or logs. Model effectiveness must be measured, not promised. Use PostgreSQL locally and Neon hosted.

## Phase 13 risk operations verification

| Requirements | Implementation | Evidence |
| --- | --- | --- |
| FR-13, FR-14, FR-17 | Completed-run selector, backend-filtered payout queue, decision totals, and recorded historical trends | [Overview](../frontend/src/pages/dashboard-page.tsx), [queue](../frontend/src/pages/payout-queue-page.tsx), [component tests](../frontend/tests/risk-operations.test.tsx). Unknown daily coverage is shown separately from known non-settlement zeroes. |
| FR-12, FR-18, FR-30 | Published transaction evidence, team and profession baselines, fraud paths, and Isolation Forest validation and fallback | [Investigation](../frontend/src/pages/payout-detail-page.tsx), [fraud insights](../frontend/src/pages/fraud-page.tsx), [model insights](../frontend/src/pages/models-page.tsx). Results are loaded from authenticated persisted APIs. |
| FR-15, FR-16, FR-19, FR-21, FR-31, FR-32 | Versioned overrides with confirmation, conflict refresh, scoped audit history, and full-run CSV download | [Override form](../frontend/src/features/risk/override-dialog.tsx), [risk API contracts](../frontend/src/services/risk.ts), [component tests](../frontend/tests/risk-operations.test.tsx). The original decision and published findings are retained. |

## Phase 12 frontend verification

| Requirements | Implementation | Verification |
| --- | --- | --- |
| FR-21 and FR-24 | [Session and permissions](../frontend/src/features/auth/session.tsx), [typed same-origin client](../frontend/src/services/api.ts), and [explicit run selection](../frontend/src/features/evaluations/selection.ts) | Component coverage for login, session restoration and expiration, cookie-bound CSRF, role actions and scoped cache cleanup. |
| FR-26, FR-27, FR-28 and FR-30 | [Dataset workflow](../frontend/src/pages/datasets-page.tsx), [CSV and synthetic preset forms](../frontend/src/features/datasets/import-panel.tsx), and [evaluation workflow](../frontend/src/pages/evaluations-page.tsx) | [Component tests](../frontend/tests/workflows.test.tsx) cover upload validation, server errors, import replay, protected evaluation, and explicit selection of completed runs. |
| Responsive workspace | [Workspace layout](../frontend/src/layouts/app-layout.tsx) and [theme](../frontend/src/index.css) | Synthetic browser verification uses the Phase 11 backend and checks 360, 768, 1024 and 1440 CSS px. |

## Phase 3 traceability

| ID | Implemented functionality | Implementation and tests | Status |
| --- | --- | --- | --- |
| FR-21 | Login, current user, logout, user provisioning, live role enforcement | [Auth routes](../backend/app/api/auth.py), [role dependencies](../backend/app/api/dependencies.py), [provisioning CLI](../backend/scripts/create_user.py), [PostgreSQL auth tests](../backend/tests/integration/test_auth.py) | Implemented and verified against synthetic PostgreSQL. Future domain routes still need operation-specific permissions. |
| NFR-07 | Argon2id, signed expiring cookies, CSRF and Origin validation, process-local rate limits | [Security](../backend/app/core/security.py), [rate limiter](../backend/app/core/rate_limit.py), [security tests](../backend/tests/unit/test_security.py), [auth integration tests](../backend/tests/integration/test_auth.py) | Phase 3 controls verified locally, including Secure cookie behavior under simulated HTTPS. Hosted deployment verification is pending. |
| FR-32 | Trusted actor and review scope for future audit writes | [Actor context](../backend/app/core/review_scope.py), [authorization rules tests](../backend/tests/unit/test_authorization_rules.py), [scope integration tests](../backend/tests/integration/test_auth.py) | Actor and scope foundation verified. Override API and effective history workflows are pending. |

## Phase 4 traceability

Phase 5 additionally revalidates pending payouts against the committed import boundary during evaluation. See the Phase 5 table below.

| ID | Implemented functionality | Implementation and tests | Status |
| --- | --- | --- | --- |
| FR-01, FR-25, NFR-01 | Exact CSV headers, typed values, safe source IDs, integer cents, IST dates, and required references | [CSV normalization](../backend/app/services/csv_source.py), [CSV tests](../backend/tests/unit/test_csv_source.py), [PostgreSQL ingestion tests](../backend/tests/integration/test_ingestion.py) | Verified with fabricated fixtures and PostgreSQL. |
| FR-02, FR-03 | Pending reconciliation by team and inclusive date coverage, preserved non-captured attempts, and historical completeness findings | [Reconciliation](../backend/app/services/reconciliation_service.py), [settlement and ingestion tests](../backend/tests/unit/test_csv_source.py), [PostgreSQL reconciliation tests](../backend/tests/integration/test_ingestion.py) | Verified, including weekend coverage and rejection of overlaps and count/amount mismatches. |
| FR-26, FR-27, FR-28 | Initial and incremental import, authenticated CLI, deterministic replay, conflict handling, one-time disposition completion, atomic persistence, and typed membership | [Ingestion service](../backend/app/services/ingestion_service.py), [local CLI](../backend/scripts/import_dataset.py), [PostgreSQL ingestion tests](../backend/tests/integration/test_ingestion.py) | Verified, including concurrent requests, rollback, and database reads after deleting synthetic input files. |
| FR-21, FR-22, FR-33, FR-34, NFR-06, NFR-07 | Admin upload, shared dataset scope, batch list and details, bounded multipart parsing, sanitized errors, and allowlisted demo preset | [Dataset routes](../backend/app/api/datasets.py), [multipart processing](../backend/app/api/dataset_upload.py), [demo preset](../sample_data/synthetic-v1/), [HTTP tests](../backend/tests/integration/test_ingestion.py) | API behavior verified with the synthetic database. Frontend workflow and hosted deployment remain pending. |

## Phase 5 traceability

| ID | Implemented functionality | Implementation and tests | Status |
| --- | --- | --- | --- |
| FR-02, FR-28, FR-29 | Scoped READY batch selection, exact pending membership, committed import boundary, payout revalidation, and database-only read-only REPEATABLE READ materialization | [Evaluation inputs](../backend/app/services/evaluation_input_service.py), [snapshot queries](../backend/app/repositories/evaluation_inputs.py), [PostgreSQL evaluation tests](../backend/tests/integration/test_evaluation.py) | Verified with synthetic backfills, unchanged membership, and source files deleted. Feature extraction and ML are pending. |
| FR-05, FR-06, FR-07, FR-17 | Typed normal-history and fraud candidates, separate cutoff and availability clocks, historical completeness checks, and frozen 20-day monitoring inputs | [Eligibility and monitoring](../backend/app/risk_engine/eligibility.py), [policy tests](../backend/tests/unit/test_evaluation_policy.py), [PostgreSQL evaluation tests](../backend/tests/integration/test_evaluation.py) | Input preparation verified. Baselines, graph analysis, and dashboard remain pending. |
| FR-21, FR-24, FR-30, NFR-02, NFR-06 | Server-side actor and scope, stable input fingerprint, sanitized manifest, advisory-lock run creation, and atomic claim/failure transitions | [Run service](../backend/app/services/evaluation_service.py), [manifest](../backend/app/services/evaluation_manifest.py), [PostgreSQL evaluation tests](../backend/tests/integration/test_evaluation.py) | Verified for Phase 5. Model metadata, publication and completion are later phases. |

## Phase 6 traceability

| ID | Implemented functionality | Implementation and tests | Status |
| --- | --- | --- | --- |
| FR-02, FR-03, FR-04, NFR-01 | Versioned, ordered features over the accepted PostgreSQL payout window, exact payable cents, and distinct missing-value states | [Feature definitions](../backend/app/risk_engine/feature_schema.py), [feature extraction](../backend/app/risk_engine/features.py), [unit tests](../backend/tests/unit/test_statistics.py), [PostgreSQL pipeline tests](../backend/tests/integration/test_statistical_pipeline.py) | Verified on fabricated records, including deleted inputs and a late capture. |
| FR-05, FR-06, FR-09 | Time-qualified historical cohorts, comparable coverage, balanced leave-one-team-out peer baselines, Median/IQR, materiality, and sparse fallback | [Baselines](../backend/app/risk_engine/baselines.py), [comparisons](../backend/app/risk_engine/statistical_anomalies.py), [chronological analysis](../backend/app/services/statistical_analysis_service.py), [unit tests](../backend/tests/unit/test_statistics.py) | Verified. Thresholds are engineering assumptions. |
| FR-12, FR-18, NFR-03, NFR-06 | Typed statistical findings with computable explanations, source IDs, sample counts, and safe persistence shape | [Findings](../backend/app/schemas/risk_findings.py), [PostgreSQL pipeline tests](../backend/tests/integration/test_statistical_pipeline.py) | Statistical evidence verified. Final reasons and user-facing investigation remain scheduled. |
| FR-10, FR-29, NFR-02 | Ordered numeric model contract, separate metadata and availability, policy fingerprinting, and historical features with separate event and import cutoffs | [Analysis service](../backend/app/services/statistical_analysis_service.py), [manifest](../backend/app/services/evaluation_manifest.py), [unit tests](../backend/tests/unit/test_statistics.py) | Feature contract verified. Isolation Forest and training-only preprocessing are scheduled for Phase 8. |
| FR-07, FR-08, FR-11, FR-30 | Fraud graph analysis, final decisions, and publication | Phase 7 fraud analysis, Phase 8 ML, and Phase 9 publication | Scheduled for subsequent phases. |

## Phase 7 traceability

| ID | Implemented functionality | Implementation and tests | Status |
| --- | --- | --- | --- |
| FR-07, NFR-02 | Cutoff-known seeds, validated note references and local mappings, typed graph, strong projection and deterministic key | [Evidence](../backend/app/risk_engine/fraud_evidence.py), [graph](../backend/app/risk_engine/fraud_graph.py), [clusters](../backend/app/risk_engine/fraud_clusters.py), [unit tests](../backend/tests/unit/test_fraud.py) | Verified against fabricated cases. |
| FR-08, FR-12, NFR-03, NFR-06 | Direct and corroborated exposure, non-payable and incidental leads, safe typed paths, deterministic primary and alternatives | [Rules](../backend/app/risk_engine/fraud_rules.py), [findings](../backend/app/schemas/fraud_findings.py), [tests](../backend/tests/unit/test_fraud.py) | Evidence and findings verified. Final reasons and decisions remain scheduled. |
| FR-02, FR-06, FR-28, FR-29 | Shared PostgreSQL snapshot, retained pending boundary, time-qualified contamination, and recomputed statistical cohorts | [Analysis](../backend/app/services/fraud_analysis_service.py), [unit tests](../backend/tests/unit/test_fraud.py), [PostgreSQL tests](../backend/tests/integration/test_fraud_pipeline.py) | Verified. Isolation Forest remains scheduled for Phase 8. |
| FR-11, FR-30, FR-31 | Final cluster, model, and decision publication | Later evaluation phases | Scheduled. Phase 7 supplies typed drafts. |

### Phase 8 verification

| Requirements | Implementation | Evidence | Status |
| --- | --- | --- | --- |
| FR-10, FR-29, NFR-02 | PostgreSQL-snapshot inputs with Phase 7 contamination filtering, chronological training/calibration/holdout, training-only preprocessing and deterministic refitting | [Training](../backend/app/ml/training.py), [preprocessing](../backend/app/ml/preprocessing.py), [unit tests](../backend/tests/unit/test_ml.py), [PostgreSQL test](../backend/tests/integration/test_ml_pipeline.py) | Verified with synthetic inputs. Removing the input CSVs does not affect evaluation. |
| FR-10, FR-29 | Fixed calibration, holdout workload gates, explicit fallback and evidence-backed inference | [Validation](../backend/app/ml/validation.py), [inference](../backend/app/ml/inference.py), [unit tests](../backend/tests/unit/test_ml.py) | Active and fallback paths verified. The bundled synthetic scenario was diagnostic-only because none of its four holdout flags met the actionability gate. |
| FR-30, FR-31 | Persistence-compatible model metadata and findings with review eligibility | [Typed contracts](../backend/app/schemas/ml_findings.py), [analysis service](../backend/app/services/ml_analysis_service.py), [PostgreSQL test](../backend/tests/integration/test_ml_pipeline.py) | Drafts and preservation of prior decisions verified. Atomic publication is scheduled for Phase 9. |

### Phase 9 verification

| Requirements | Implementation | Evidence | Status |
| --- | --- | --- | --- |
| FR-07, FR-08, FR-09, FR-10, FR-11 | Hybrid precedence, safety fallback, typed evidence and actionable explanations | [Policy](../backend/app/risk_engine/hybrid_decisions.py), [evidence validation](../backend/app/risk_engine/decision_evidence.py), [unit tests](../backend/tests/unit/test_hybrid_decisions.py) | Verified on synthetic inputs. |
| FR-29, FR-30, FR-31 | Single-snapshot execution, compatible model reuse, frozen monitoring, atomic publication, and stale-attempt recovery | [Execution](../backend/app/services/evaluate_batch.py), [publication](../backend/app/services/evaluation_publication.py), [PostgreSQL tests](../backend/tests/integration/test_hybrid_evaluation.py) | Verified against PostgreSQL with 20-payout and general batches, rollback, concurrent requests, retries and prior override preservation. |
| FR-30 | Explicit local batch selection and authenticated CLI | [CLI](../backend/scripts/evaluate_batch.py), [PostgreSQL tests](../backend/tests/integration/test_hybrid_evaluation.py) | Verified with synthetic admin credentials. Public evaluation and result APIs are assigned to later phases. |

### Phase 10 verification

| Requirements | Implementation | Evidence | Status |
| --- | --- | --- | --- |
| FR-12, FR-15, FR-16, FR-21 | Authenticated override, scoped audit timeline, published-snapshot evidence validation and stale-version conflict | [API](../backend/app/api/payout_decisions.py), [service](../backend/app/services/decision_overrides.py), [PostgreSQL tests](../backend/tests/integration/test_decision_overrides.py) | Verified with synthetic users and PostgreSQL, including concurrent writes and rollback. |
| FR-20, FR-24, FR-31, FR-32 | Original automated result and previous events retained, shared scoped effective decision projection, and no override propagation or training-label conversion | [Projection query](../backend/app/repositories/payout_decisions.py), [projection tests](../backend/tests/unit/test_override_projection.py), [PostgreSQL tests](../backend/tests/integration/test_decision_overrides.py) | Verified across run re-evaluation, application restart and independent hosted reviewer scopes. Phase 11 consumers remain scheduled. |

### Phase 11 verification

| Requirements | Implementation | Evidence | Status |
| --- | --- | --- | --- |
| FR-13, FR-14, FR-17, FR-18, FR-30, FR-31 | Evaluation and model results, scoped queue and investigation, frozen monitoring, and run-specific fraud context | [Routes](../backend/app/api/risk_results.py), [contracts](../backend/app/schemas/risk_results.py), [result service](../backend/app/services/risk_results.py), [PostgreSQL HTTP tests](../backend/tests/integration/test_risk_operations.py) | Synthetic verification covers the authenticated upload, evaluate, investigate, override and history workflow. |
| FR-19, FR-21, FR-32 | Authorized full-run export using the shared effective decision projection and one read-only snapshot | [CSV renderer](../backend/app/services/decision_export.py), [unit tests](../backend/tests/unit/test_risk_read_contracts.py), [PostgreSQL HTTP tests](../backend/tests/integration/test_risk_operations.py) | Synthetic verification covers 20 and two-payout exports, concurrent overrides, formula protection and reviewer isolation. |
