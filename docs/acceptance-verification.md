# Phase 14 acceptance verification

## Scope and import diagnosis

Phase 14B reviewed all five pages of the supplied brief-only `Payout_Risk_Desk_Candidate_Brief.pdf`, which is ignored by Git. The explicit Phase 14 checklist, [codex_prompt.md](../codex_prompt.md), [requirements](requirements.md), and [business rules](business-rules.md) cover subsequent project extensions. The acceptance environment used independently fabricated data. Deployment and the actual private assessment run require separate verification.

Previously, the local administrator form started collapsed behind **Import dataset**. Analysts and viewers see history only. A hosted demo administrator can import the allowlisted synthetic preset, while the API denies arbitrary multipart uploads. The local administrator now lands on the initial-import form at **Datasets**, with an explicit **Initial or incremental import** action when inspecting a saved batch. Read-only users see an explanation of the access policy. These conditions were reproducible with controlled sessions. The developer’s existing private session and app mode were outside verification scope.

Local `main` at `6ca79d0` contains both Phase 13 frontend and the completed backend. The checked-out `frontend` tip `e0beb28` is four commits behind and is an ancestor of `main`. No branch or schema was changed. The verification build used the combined Git revision in an isolated directory, the local Phase 14 changes, and the Phase 14B backend changes. Follow the one-time integration steps below to run directly from the repository's `backend` directory.

The first combined backend run found a Windows regression in the historical CSV **test**. It assumed that a quoted field contained LF even when Git checked the fixture out as CRLF. The mapping test now supplies explicit LF bytes. A regression test checks both input variants, preserving the complete quoted text and the physical row number in each. Production parsing was already preserving the original content.

A live queue regression showed that changing the sort field and direction quickly could discard the field in the URL and sort by payout ID. The queue now keeps an in-flight copy of its URL parameters until navigation renders. A focused component test reproduced the failure before the fix and checks both parameters on the same API request.

## Original five-page brief crosswalk

| Page | Requirements or deliverables | Evidence and disposition |
| --- | --- | --- |
| 1 | Eight home-service professions, card/Tap to Pay/ACH, calendar date in IST, weekday T+2, 17:30 creation, one payout per team/day, weekend collections grouped with Monday, captured base plus tip only, one decision, normal approval and atypical manual review | FR-01–06, FR-09, FR-11 and NFR-01. Phase 14B reproduced and fixed disjoint duplicate settlements. PostgreSQL regression covers disjoint and overlapping windows. |
| 2 | Reject only for exposure to a known fraud case, actionable plain-English reasons and cited records, cluster for each rejection, any override with reason and audit, exact 20 pending on the defined scenario dates, historical learning, four dashboard functions | FR-04–09, FR-11–20 and FR-32. The suite walks each of the six directed override transitions on a payout with supported evidence. Newly discovered fraud with no historical cluster remains an interpretation question under FR-15. The PDF leaves layout open and treats login, roles and deployment as optional. They remain requirements of this project. |
| 3 | Exact six-column export, valid source IDs and one or two actionable sentences, dashboard walkthrough and screen share, deliver code and CSV after the call, keep actual data local, stated team count and profession values | FR-12, FR-19, FR-23, FR-25, NFR-03 and NFR-06. Chrome downloaded all 20 synthetic rows and verified IDs, labels, fields and effective reasons. Real screen share, walkthrough and handoff are deferred to the candidate's session. Updated instructions call for repository and deployment links. |
| 4 | Complete business/team, historical and action-day transaction fields, payment status and method values, blank ACH fingerprint, exact calendar ranges, historical payout fields and six reason codes | FR-01–03, FR-07, FR-08, FR-20, FR-25 and FR-29. CSV schema and persistence regressions plus the independent scenario calendar and all three fraud-code families cover these conditions. |
| 5 | Pending CSV schema and status, exact 20 memberships, creation Thursday 12 November 17:30 IST and Tuesday 10 November coverage | FR-01, FR-02, FR-11, FR-19 and FR-26. The browser uploaded the five files and the oracle verified memberships, header, dates, cents and captured counts. |

The interview schedule, permitted coding tools, candidate discussion and submission deadline are session instructions. They do not require application features.

## Evidence key

Test paths under `backend/tests` below refer to the combined checkout. **Browser** means the real Chrome/FastAPI/PostgreSQL run in [acceptance_browser.mjs](../scripts/acceptance_browser.mjs). **Oracle** means [verify_assessment.py](../scripts/verify_assessment.py), which independently reconciles captured counts and cents, computes historical daily categories from CSV dispositions, then verifies the exact download against persisted effective decisions. The [fixture generator](../scripts/assessment_fixture.py) imports no application code.

| Key | Automated evidence |
| --- | --- |
| ING | `integration/test_ingestion.py`, `unit/test_csv_source.py` |
| SQL | `integration/test_schema.py`, standalone Alembic check and PostgreSQL table count |
| SNAP | `integration/test_evaluation.py`, `unit/test_evaluation_policy.py` |
| STATS | `unit/test_statistics.py`, `integration/test_statistical_pipeline.py` |
| FRAUD | `unit/test_fraud.py`, `integration/test_fraud_pipeline.py` |
| ML | `unit/test_ml.py`, `integration/test_ml_pipeline.py` |
| PIPE | `unit/test_hybrid_decisions.py`, `integration/test_hybrid_evaluation.py` |
| AUDIT | `unit/test_override_projection.py`, `integration/test_decision_overrides.py` |
| RESULTS | `unit/test_risk_read_contracts.py`, `integration/test_risk_operations.py` |
| AUTH | `unit/test_security.py`, `integration/test_auth.py` |
| UI | `frontend/tests/workflows.test.tsx`, `frontend/tests/risk-operations.test.tsx` |

## Requirement matrix

The expected behaviour is derived from the specifications and handwritten fixture calendar, with fixed amounts and risk cases independent of the application implementation. The matrix also accounts for the brief tasks where the project extends them.

| ID | Implementation | Positive and negative or boundary check | Expected | Actual and verification |
| --- | --- | --- | --- | --- |
| FR-01 | `csv_source.py`, datasets API | ING, Browser | Five schemas accepted. Missing/duplicate/extra headers, bad dates/enums, missing team rejected | **PASS** – all typed inputs imported. Malformed input blocked and retry produced READY. |
| FR-02 | `reconciliation_service.py`, snapshot inputs | ING, SNAP, Oracle | Inclusive IST dates; T+2. Captured cents plus tips. Wrong count/amount, overlap and later backfill cannot change fixed membership | **PASS** – settlement and late-arrival cases passed; 40 captured attempts reconciled to 212,810 cents. |
| FR-03 | `repositories/evaluation_inputs.py`, investigation | ING, STATS, Browser | Declines and refunds retained. Zero contribution to payable cents | **PASS** – each published payout retained its declined and refunded attempt. |
| FR-04 | `risk_engine/features.py` | STATS | Amount/count/attempts, median and maximum, rates, tips, cards and velocity use correct observations and units | **PASS** – feature assertions passed against explicit cents, ratios and time windows. |
| FR-05 | Statistical analysis and baselines | STATS | Hand-calculated quartiles. Team history. Peers exclude subject. Peer weighting independent of volume | **PASS** – hand-calculated quartiles and independently weighted profession peers matched. |
| FR-06 | `risk_engine/eligibility.py`, feature/baseline policies | SNAP, STATS, FRAUD | Missing differs from zero. Matched durations. Sparse fallback. Victim and suspended legitimate history. Future/contaminated history excluded | **PASS** – sparse, missing, zero-spread, eligibility and point-in-time test oracles passed. |
| FR-07 | `risk_engine/fraud_graph.py` | FRAUD, Browser | Known rejected case seeds. Strong clusters stay stable. Ambiguous notes and shared weak links do not merge cases | **PASS** – supported seeds and deterministic strong-link clustering verified, including ambiguous-note controls. |
| FR-08 | Fraud analysis service, run findings | FRAUD, PIPE, Browser | Captured implicated card may establish exposure. Declined/refunded, victims, remediated cases, bank-only and blank IDs cannot alone | **PASS** – captured exposure rejected the expected payout. Nonpayable links stayed in review. |
| FR-09 | Statistical analysis service | STATS, Browser | High/low feature-specific materiality. Zero IQR and zero denominator handled | **PASS** – the fictional high-volume payout went to review. Threshold boundary unit checks passed. |
| FR-10 | `ml_analysis_service.py`, model metadata | ML, Browser | Actual fit, validation and finite inference. Past-only training. Diagnostic and failure statuses explicit; ML alone may review | **NOT VERIFIED** for live ACTIVE influence. Real PostgreSQL training and all 20 scores succeeded. The published status was DIAGNOSTIC_ONLY. The actual fitted ACTIVE/actionable case passed at unit level. |
| FR-11 | Hybrid evaluation and persistence | PIPE, Browser, Oracle | One decision per exact selected payout, 20 initial. Invalid reconciliation prevents evaluation | **PASS** – exact 20 initial decisions and one incremental result. Invalid import did not become READY. |
| FR-12 | Decision explanations and read projections | PIPE, RESULTS, Browser, Oracle | Actionable review and reject reasons, relevant typed IDs, same-run fraud cluster | **PASS** – run-specific cluster, typed links, and reasons survived persisted read and export verification. |
| FR-13 | Payout queue/detail API and pages | RESULTS, UI, Browser | All selected payouts are inspectable with safe attempt and decision details | **PASS** – the browser opened all twenty payout details and loaded transactions, findings and audit data. |
| FR-14 | Payout queue, results repository | RESULTS, UI, Browser | Search/filter/sort/paging. Empty matching filter. Filters do not affect export | **PASS** – fixed the rapid sort/direction race. Search, filters, pagination and navigation regression coverage passed. |
| FR-15 | Override API and dialog | AUDIT, UI, Browser | Authorized changes require reason; Reject needs established exposure. Stale versions rejected | **NOT VERIFIED** against independent PDF intent for newly discovered fraud. Implemented policy exercised separately in BR-06. |
| FR-16 | Immutable decision/history tables | SQL, AUDIT, Browser | Original decision retained. Each change appends actor and timestamp. Failure preserves state | **PASS** – version 1 remained intact and persisted overrides had authenticated actor/time. |
| FR-17 | Dashboard and historical monitoring | SNAP, RESULTS, Browser, Oracle | Four effective categories sum to total. Exact 20-day historical ledger. Manual approvals closed. Unknown days differ from zero | **PASS** – exact current cents/counts and all 20 historical days matched. Old aggregates froze. |
| FR-18 | Fraud, payout and model detail pages | FRAUD, STATS, RESULTS, UI, Browser | Safe paths and team/peer baselines. No raw restricted fields | **PASS** – statistical and fraud evidence panels rendered from persisted responses. Redaction tests passed. |
| FR-19 | `decision_export.py`, export API/UI | RESULTS, Browser, Oracle | Exact six columns, labels, membership and evidence. Full-run CSV. Scoped. Snapshot-consistent. Formula prefixes escaped | **PASS** – actual browser CSV and every row matched effective records. Concurrent-export snapshot and spreadsheet tests passed. |
| FR-20 | Verified disposition import | ING, SNAP, AUDIT | Original financial values unchanged. Eligible subsequent verified disposition only. Unverified overrides never train | **PASS** – verified disposition completion worked once and application overrides stayed out of training. |
| FR-21 | Auth API, permission guards, provisioning, frontend | AUTH, ING, RESULTS, UI, Browser | Argon2id provisioned users only; ADMIN/ANALYST/VIEWER. No signup; CSRF, expiry, inactive, revocation, rate limiting and scoped restrictions | **PASS** – synthetic provisioned admin logged in. Duplicate provisioning refused without resetting login. Role restrictions and session tests passed. No signup page or public registration route is implemented. |
| FR-22 | Health and dataset API | SQL, AUTH, Browser | Health remains public and safe; DB readiness and imported batch state are observable | **PASS** – health/readiness and import state verified. Outages receive sanitized failures. |
| FR-23 | Deployment and submission | Release verification | Working synthetic deployment and repository/submission links | **DEFERRED WITH REASON** – deployment and submission are outside Phase 14. |
| FR-24 | Run service and explicit run selection | SNAP, PIPE, Browser | Exact replay reused. Changed input/config creates another run. Old decisions and overrides retained | **PASS** – idempotence, changed digests, atomic publication and prior-run preservation passed. |
| FR-25 | SQLAlchemy source mappings | SQL, ING | Every source field typed. Leading zeroes preserved. No raw CSV storage | **PASS** – exactly eleven application tables. Tested typed columns, leading zeros, nulls and metadata-only storage. |
| FR-26 | Datasets page, CSV form, upload API | UI, ING, Browser | Local administrator sees five initial fields. Two required incremental. Permission checks remain enforced | **PASS** – local-admin form visible by default. Live five-file and two-file workflows published results. |
| FR-27 | Ingestion and reconciliation services | ING | Identical input idempotent. Conflicts, invalid data and simulated DB failure roll back. Concurrency and verified disposition handled | **PASS** – PostgreSQL tests exercised duplicates, conflicting imports, concurrent claims and rollback. |
| FR-28 | Batch metadata and source membership | SQL, ING, SNAP | Stored checksum, sizes/counts and source IDs. Unchanged records still members. No source blobs | **PASS** – source membership/checksums and accepted unchanged IDs survived input removal. |
| FR-29 | Snapshot and ML input services | SNAP, ML, Browser | Files gone before evaluation. Cutoff and availability respected. Only eligible history fits/validates | **PASS** – evaluation started after uploading files and removing its temporary inputs. Eligible history and leakage tests passed. |
| FR-30 | Model, run and decision publication | SQL, ML, PIPE, RESULTS | Atomic persisted ML score/status/metadata and evidence. No partial completed runs | **PASS** – model status, scores, findings and result versions published atomically. |
| FR-31 | Completed-run selector, historical reads | RESULTS, UI, Browser | Older runs selectable. Reevaluation creates different immutable result set. Overrides stay scoped to old run | **PASS** – the second run coexisted with the original and explicit older-run navigation worked. |
| FR-32 | Override API, audit table and timeline | AUDIT, SQL, Browser | Actor from authenticated session. Version 1 and newest status correct. Concurrent stale request fails | **PASS** – append-only history displayed effective v2 while the automated version stayed immutable. Stale and concurrency regressions passed. |
| FR-33 | Datasets, evaluations, model and risk pages | UI, ING, PIPE, Browser | Honest sync/lifecycle, failed upload retry, validation location, explicit ML limitations | **PASS** – live validation failure, retry, readiness, completed evaluation and model status. Failed-run paths tested through backend. |
| FR-34 | Dataset guards and demo import | ING, AUTH, RESULTS, UI | Demo refuses arbitrary upload before body consumption, allows only published preset. Reviewer isolation | **PASS** in PostgreSQL API and frontend component tests. Hosted browser and deployed origin verification remain separate. |
| NFR-01 | Monetary parser, reconciliation, PostgreSQL BIGINT | ING, SQL, Oracle | Exact integer cents including tips. Excess precision rejected | **PASS** – every pending CSV amount and published cent total independently reconciled. |
| NFR-02 | Input digest, model seed, run lifecycle | ML, PIPE, SNAP | Repeat gives same outcomes and run. History/config changes invalidate compatibility | **PASS** – deterministic model refit, input fingerprint, exact replay, and compatibility invalidation. |
| NFR-03 | Typed evidence and explanations | STATS, FRAUD, RESULTS, Oracle | Evidence resolves to actual relevant source records and supported paths | **PASS** – typed evidence resolves to current input and historical evidence paths in the verified paths and focused regressions. |
| NFR-04 | Override transaction and audit uniqueness | AUDIT, SQL | One concurrent writer for expected version. Rollback and immutable events | **PASS** – competing writer loses with 409. Failure leaves immutable event chain intact. |
| NFR-05 | Durable PostgreSQL results | AUDIT, SQL, PIPE | State survives app sessions and restart. Rollback preserves earlier runs | **PASS** – restart integration re-opened the app and matched the prior persisted timeline. |
| NFR-06 | Privacy restrictions, response schemas, Git exclusions | AUTH, ING, RESULTS, diff review | No private source fields through public outputs. No checked-in credentials or source data | **PASS** for synthetic tests and committed-code review. Hosted packaging remains deferred. |
| NFR-07 | Roles, signing, CSRF, origin, rate limits, same-origin client | AUTH, RESULTS, ING, UI, Browser | Tampering or wrong roles rejected; HTTPS Secure cookie contract. Hosted TLS verified separately | **PASS** for local and simulated demo controls. Actual deployed TLS remains deferred. |
| NFR-08 | Modular typed application and test suites | Ruff, TypeScript, build and regression results | Clean project checks. No schema drift or added infrastructure | **PASS** – Ruff, frontend lint/types/build and schema consistency checks passed. |
| NFR-09 | Responsive layouts and accessible controls | UI, Browser | No page overflow at 360/768/1024/1440 px. Keyboard, focus and contrast included in full usability gate | **NOT VERIFIED** in full. Four viewport no-overflow checks passed. Exhaustive focus and contrast audit and complete control-by-control coverage were not executed. |
| NFR-10 | Synchronous evaluation and persisted read API | Browser timings | Provisional local evaluation <5 s, persisted API <500 ms | **FAIL** against the provisional evaluation target. The persisted dashboard API measurement was within target. See timings below. |
| NFR-11 | Health/readiness, sanitized logging | AUTH, SQL, ING, Browser | No stack traces or raw input in unsuccessful responses. Public health reports relevant readiness | **PASS** – liveness, database readiness, startup and sanitized error handling verified. |
| NFR-12 | Ingestion rollback, publication gate, decision fallbacks | ING, PIPE, ML | Bad input blocks. Quality failures explicit. No silent approval through invalid input | **PASS** – invalid input prevented publish. Validated isolation forest fallback stayed explicit. |
| NFR-13 | Locks/pins, environment example, synthetic seed | Build and later deployment checks | Reproducible clean-container deployment and seeded hosted verification | **DEFERRED WITH REASON** – container and hosted verification belong to Phase 15. |

## Business rules and submission checks

| Check | Implementation / test | Expected and disposition |
| --- | --- | --- |
| Settlement calendar and inputs | Reconciliation, ING, Oracle | All weekday mappings including combined Saturday/Sunday/Monday. Exact 17:30 IST. Midnight coverage. Only captured attempts counted. See FR-01–03. |
| BR-01 and BR-04 | Hybrid decision policy, PIPE, Browser | Exactly one effective result per pending payout. Normal supported activity can approve. See FR-11 and FR-19. |
| BR-02 and BR-08 | Fraud graph and hybrid policy, FRAUD, ML, PIPE | Valid exposure takes priority. No anomaly-only Reject. Stable cluster resolution. See FR-07–12. |
| BR-03, BR-05 and BR-07 | Decision explanations, ING, PIPE, Oracle | Review for material anomaly/uncertainty. Actionable reasons and typed IDs. Invalid input rejected. See FR-09–12. |
| BR-06, original brief override wording | Override service / AUDIT / Browser | Known case Reject requires a same-run cluster and a published exposure path. An analyst can review new suspected fraud, but cannot reject a newly discovered case unless a validated cluster already exists. The brief summary says all original decisions can be overridden with a reason. Whether newly discovered fraud must permit immediate human rejection remains **NOT VERIFIED**. |
| Historical labels, victims, eligibility, book of record | Eligibility, model pipeline, monitoring, SNAP, FRAUD, ML, Oracle | Resolved historical reviews remain separate from open review. No unverified feedback becomes training truth. See FR-06–10 and FR-17–20. |
| Correct effective download and submission | Decision export, RESULTS, Browser, Oracle | Export the latest authorized state, ordered by payout ID, with formula safety. Actual 20-payout export, private evidence review, interviewer submission and working deployment link are **DEFERRED WITH REASON** – only the developer can process private files, and deployment is Phase 15. |

## Synthetic reconciliation and benchmark

The fixture contains 24 fictional teams across eight professions, 336 historical payouts, and 20 pending payouts. The independently expected results are **P-01 Reject** for a captured payment using the card explicitly implicated by a historical fraud case; **P-02 and P-03 Manual review** for exposure through an unsuccessful/refunded attempt respectively; **P-04 Manual review** for a material amount deviation. And **P-05 through P-20 Approve**. A shared device on P-06 is incidental. The test checks the formula (9,150 + 27n) cents per payout, plus (24,140) cents on P-04. The browser compares each result and the verifier reconciles 40 captured payments and $2,128.10 across the whole batch. The derived totals are:

| Category | Original count | Original cents | Count after administrator override | Cents after override |
| --- | ---: | ---: | ---: | ---: |
| Automatically approved | 16 | 151800 | 16 | 151800 |
| Analyst approved | 0 | 0 | 1 | 9177 |
| Open manual review | 3 | 51833 | 3 | 51833 |
| Rejected | 1 | 9177 | 0 | 0 |
| Total | 20 | 212810 | 20 | 212810 |

The synthetic increment has one ACH capture for $100.00 plus a $2.25 tip. Its independently expected total is 1 payout and 10,225 cents. The prior run retains its 20 decisions and the original automated Reject, and the frozen comparison covers October 23 through November 11.

The independently checked historical totals were 331 automatic approvals for 4,523,083 cents, 2 resolved analyst approvals for 72,666 cents, and 3 rejections for 27,612 cents. There were 14 recorded settlement days and six non-settlement zero days. A separate fixture and denominator tests covered unknown weekdays.

Isolation Forest fitted 189 historical observations, scored 48 calibration and 96 holdout observations, and produced finite scores for all 20 pending payouts. The threshold was 0.7101109396. The real browser run published `DIAGNOSTIC_ONLY` because the validation had zero actionable alerts. Statistics and known fraud therefore drove the decision policy for this batch.

The separately rerun PostgreSQL ML integration fixture also remained diagnostic. Its 129 fitted and 69 holdout observations produced four validation anomalies, one from ten verified outliers. The actionability gate kept these alerts from influencing decisions. The unit fixture passed all gates using a genuine fitted model and produced an actionable pending signal.

The final browser run took 15.72 seconds for the assessment evaluation, 14.99 seconds for the incremental evaluation, and 95 ms for a persisted dashboard read. The backend profile on the earlier fixture measured about 19.7 seconds with profiler overhead. Historical statistical preparation accounted for 8.3 seconds and 164 individual model scoring calls accounted for 8.1 seconds. Input capture plus related overhead was under a second. Batch-vector scoring and reuse of prefiltered comparable cohorts are the main optimization candidates. The provisional five-second evaluation target remains unmet.

Five warm in-process ASGI requests per endpoint against the final PostgreSQL database measured median times of 47.4 ms for dashboard, 52.9 ms for queue, 93.9 ms for investigation and 46.5 ms for CSV. The measurements include the real query and serialization but exclude network and browser overhead.

## Commands and coverage limits

The PostgreSQL regression database used a dedicated loopback port. The application's browser database was separate from the empty database used for the migration/regression suite. These are the primary commands, with database settings established in the relevant shell:

```text
python -m pytest -c backend/pyproject.toml backend/tests -q
python -m ruff check --no-cache --no-respect-gitignore --exclude __pycache__ backend
python -m ruff format --no-cache --check --no-respect-gitignore --exclude __pycache__ backend
python -m alembic -c backend/alembic.ini upgrade head
python -m alembic -c backend/alembic.ini check
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run test
npm.cmd --prefix frontend run build
python scripts/assessment_fixture.py .venv/phase14-fixture-outlier
node scripts/acceptance_browser.mjs SYNTHETIC_FIXTURE_DIRECTORY NEW_TEST_OUTPUT_DIRECTORY
```

The last command uses a running synthetic local stack and a fresh Chrome debugging profile. Supply the loopback frontend origin, local Chrome debugging port and disposable application password in `SCANPAY_ACCEPTANCE_ORIGIN`, `SCANPAY_ACCEPTANCE_CHROME_PORT` and `SCANPAY_ACCEPTANCE_PASSWORD`. The runner expects an empty synthetic dataset store and the three separately provisioned roles `scanpay_admin`, `acceptance_analyst`, and `acceptance_viewer`. Use only the fixture generated by the preceding command.

The less frequent stale, concurrent, session expiry, demo scope and service-failure paths were verified at their API or component boundaries. A hosted browser session, a full browser focus and contrast audit, and deliberate browser injection of every API/evaluation failure were outside the executed browser coverage. The independently reviewed brief-only PDF, private-data fraud links and training manifest, actual output and reviewer acceptance also remain unverified.

**Gate result — NOT READY FOR PHASE 15.** The final regression suites had 232 backend passes and 27 frontend passes with zero failures or skips. The local real-browser scenario and the private verifier applied to its synthetic export passed. The 47 FR/NFR rows have 41 PASS, one FAIL, three NOT VERIFIED, and two DEFERRED WITH REASON. Live ACTIVE decision influence, the provisional evaluation target, comprehensive browser accessibility/error-path coverage, and the newly discovered fraud override interpretation keep the strict acceptance gate open.

## One-time repository integration

`git rev-list --left-right --count HEAD...main` returned `0 4` on the verified frontend checkout. Run these commands yourself in the repository root. The stash keeps the current tracked edits and untracked acceptance files, while ignored local files remain in place. Keep the stash until you've checked the integrated result.

```bat
git status --short
git merge-base --is-ancestor HEAD main
git stash push --include-untracked -m "Phase 14 acceptance and remediation"
git merge --ff-only main
git stash apply "stash@{0}"
git apply --check docs\phase14b-backend.patch
git apply docs\phase14b-backend.patch
git diff --check
```

Stop if any command fails. Confirm the ancestry before fast-forwarding a different checkout. The patch applies the backend-only edits to the canonical sources on the combined revision. Stage the resulting source files and tests. The patch is a handoff for this branch layout. A three-way check of the README preserved both the acceptance guide link and the existing backend CLI guidance.

## Windows CMD private assessment workflow

After the one-time integration, run the following from the repository root in **Command Prompt**. The app runs directly from the tracked backend and frontend.

```bat
set "SCANPAY_BACKEND=backend"
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r "%SCANPAY_BACKEND%\requirements-dev.txt"
```

Create a **separate private local database** using an existing PostgreSQL administrator. These two commands use the installed PostgreSQL 18 tools. They prompt rather than placing passwords in shell history.

```bat
"C:\Program Files\PostgreSQL\18\bin\createuser.exe" -h 127.0.0.1 -U postgres -P scanpay_private_owner
"C:\Program Files\PostgreSQL\18\bin\createdb.exe" -h 127.0.0.1 -U postgres -O scanpay_private_owner scanpay_private
```

Create a PostgreSQL password file outside the repository, accessible only to your Windows account. Use the PostgreSQL password given to the **database role** in this entry. PostgreSQL passfiles require escaping any `:` or `\` in the password with `\`.

```text
127.0.0.1:5432:scanpay_private:scanpay_private_owner:<database-role password>
```

```bat
set "PGPASSFILE=C:\private\scanpay-pgpass.conf"
set "SCANPAY_DATABASE_URL=postgresql+psycopg://scanpay_private_owner@127.0.0.1:5432/scanpay_private"
set "SCANPAY_MIGRATION_DATABASE_URL=%SCANPAY_DATABASE_URL%"
set "SCANPAY_APP_MODE=local"
set "SCANPAY_AUTH_COOKIE_SECURE=false"
set "SCANPAY_TRUSTED_ORIGINS=["http://127.0.0.1:5173"]"
for /f "delims=" %S in ('.venv\Scripts\python.exe -c "import secrets. Print(secrets.token_urlsafe(48))"') do @set "SCANPAY_AUTH_SIGNING_SECRET=%S"
.venv\Scripts\python.exe -m alembic -c "%SCANPAY_BACKEND%\alembic.ini" upgrade head
set "PYTHONPATH=%CD%\%SCANPAY_BACKEND%"
.venv\Scripts\python.exe -m scripts.create_user --username scanpay_admin --display-name "ScanPay Administrator" --role ADMIN
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir "%SCANPAY_BACKEND%" --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

Use `%%S` in a batch file. Provisioning prompts for the application password and confirmation. An existing username produces a duplicate error and leaves the password unchanged. For the future hosted **synthetic** database, run the same provisioning module using its explicitly selected synthetic database URL and approved hosted settings, including demo mode, a signing secret, Secure cookies and the HTTPS trusted origin. Never reuse the private local database or credentials.

In a second CMD terminal at the repository root:

```bat
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev
```

1. Open `http://127.0.0.1:5173` and sign in as the pre-registered administrator. There is no public sign-up. The **Synthetic demo** badge identifies demo mode, which offers preset imports only.
2. Open **Datasets → Import mode → Initial**. Attach `teams.csv`, `transactions_history.csv`, `payouts_history.csv`, `transactions_10nov.csv`, and `payouts_pending.csv` to their corresponding named slots. Set **Evaluation cutoff (IST)** to **13 November 2026, 10:00** and enable **Enforce assessment counts**.
3. Select **Upload dataset**. Inspect the **Ready** batch and select **Use dataset for evaluation → Start evaluation → Inspect completed run → Use this run**. Evaluation automatically fits and scores eligible history from PostgreSQL. Review the real model status and any fallback in **Model insights**.
4. Open **Payout queue** and investigate results before using **Export decisions**. Keep the downloaded CSV outside the repository. Copy the selected run ID from the URL.
5. Run the private reconciliation command below with the downloaded CSV from that same run and scope. It prompts for the existing application password and reports the total and model status. Re-export before verification if an analyst changed a decision after the download.

```bat
.venv\Scripts\python.exe scripts\verify_assessment.py --username scanpay_admin --run-id YOUR_COMPLETED_RUN_UUID --history-dir "C:\private\2_history" --action-dir "C:\private\3_action_day" --export "C:\private\decisions.csv"
```

The command uses loopback reads and never sends the source files. It verifies source dates, membership, captured cents, historical totals, decision/evidence/cluster consistency and the exact export. Review the actual fraud evidence and training manifest locally. The approval categories in historical records are not ground-truth labels for the pending batch.
## Historical fraud reconstruction addendum

Fraud evidence policy v2 can corroborate quantified named-team card rings and small-to-large card testing from the frozen historical payout observations. Ring reconstruction requires the exact stated number of shared captured cards on the same IST collection date. If the note mentions a device, the device must also match the note and the structured records for each named team. Testing reconstruction checks the stated amount range, a larger captured charge, and an earlier matching attempt at every named peer within two hours. Cross-team transaction and team IDs remain supporting evidence through publication and export. The graph still requires a captured current payment for a direct payable exposure.

Ambiguous names and mismatched witnesses do not establish exposure. The parser is limited to supported prose forms, exact normalized team names and payout-covered historical attempts in the snapshot. Uncovered attempts, informal aliases, prose-only account changes and independent behavioral similarity without a verified card link are not reconstructed by this policy. Explicit evidence directives and the internal reviewed mapping mechanism remain available. The private verifier reports supplied cases, published seeds, source references and investigative leads. An unpublished seed warrants review and does not prove missed exposure.

Synthetic API regressions use uploaded CSVs containing prose notes. Both named-team scenarios passed through PostgreSQL, publication, payout details and CSV export. The backend regression suite passed 239 tests with Alembic consistency verified. A subsequent focused run covering the new mapping reader and the local CLI passed eight tests. The frontend passed 27 tests, lint, TypeScript and build. Broader acceptance, including complete natural-language interpretation and private-case verification, remains open.

For manually reviewed cases, save a JSON file outside the repository with `version` and `cases`. Each case requires `seed_payout_id`, `transaction_ids` from the seed payout's covered history, and a timezone-aware `available_at` recording when the review was available. `implicated_profile_kinds` is optional and accepts `signup_device`, `owner_phone` and `bank_routing_and_last4`. The evaluation service validates the references and availability against the database snapshot. Changes to the review participate in the evaluation fingerprint. In Windows CMD, use the same local application environment as the backend:

```cmd
set "PYTHONPATH=backend"
.venv\Scripts\python.exe -m scripts.evaluate_batch --username scanpay_admin --batch-id YOUR_READY_BATCH_UUID --case-mapping "C:\private\reviewed-cases.json"
```

## Phase 14 historical learning and hybrid v2

The confirmed exception codes remain outside ordinary fitting, calibration and statistical baselines. Eligible complete observations are retained for holdout validation. `hybrid-v2` prioritizes material maximum-ticket and captured-volume explanations. `payout-features-v2` adds mean captured base amount and captured tips in cents. Both new comparisons are diagnostic. The maximum-ticket evidence now identifies the actual maximum payment or ties.

Named card-testing witnesses can come from teams without a historical payout. The snapshot reads only accepted records from the applicable team/day windows and retains matching fingerprints from the two hours before a captured case anchor. These attempts contribute to the evaluation fingerprint and witness provenance. They do not enter payout reconciliation or routine model observations as extra payments. The synthetic testing case includes a declined witness from a team without a payout, a documented approximate IST time and matching non-US issuer countries. The source schema still lacks payout-specific destination and bank-change events, so routing and last four digits cannot verify current bank exposure.

| Scenario | Expected and actual coverage |
| --- | --- |
| A, B, C, D, S, T | Expected one routine Approve and three reviews for the exceptional ACH, individual job and volume spike. Actual API and CSV matched. The three approved reason codes were present in the fixture and excluded from the five routine observations per baseline. Dashboard reconciled 120,000 cents. |
| E, F, H, I, J, L, O, X | Existing graph tests cover strong cluster merge, weak devices, nonpayable attempts, victim guards and ambiguity. CSV/API regressions exercise ring and testing Reject, unrelated non-Reject and idempotent replay. Full duplicate-prose and victim scenario permutations remain unverified end to end. |
| G, K | Similarity alone must never establish known fraud. Existing material statistical and validated model flags can review. No dedicated coordinated or unrelated-card sequence rule was completed. |
| M, N | Victim history does not establish exposure. The routine fixture includes historical victim context and approves normal current activity. An independently specified new victim sequence has no end-to-end verdict. |
| P, Q, R | Historical takeover is recognized as a fraud category and remediated/prior-team links cannot independently establish exposure. A true current destination match cannot be certified with the available columns. Dedicated partial-destination and remediated-victim end-to-end scenarios remain unverified. |
| U, V, W | Unit coverage verifies actionable ACTIVE model review and fraud precedence. A genuine ML-only pending decision through this complete synthetic pipeline and an independent ordinary high-value foreign-card case remain unverified. |
| Y, Z | Regression coverage protects cutoff and accepted import boundaries. The synthetic CSV and API acceptance produced one decision per payout. |

Restart the backend with its existing local PostgreSQL and signing settings, then select the same READY batch under **Evaluations** and select **Run evaluation**. Open the newly completed `hybrid-v2` run explicitly. Keep the original run selected if you need to inspect its analyst overrides. The existing batch does not need to be uploaded again.

Regression results: 244 backend tests passed, including live PostgreSQL and Alembic consistency. After adding the final clock/country and ambiguity checks, the focused unit/API run passed 36 tests, the additional parser run passed five tests, and the final CSV/API subset passed three tests. The complete suite was not rerun after those final parser changes. Frontend lint, all 27 frontend tests, TypeScript and the production build passed using Vite's runner config loader for tests and build.
