# Payout Risk Desk system design

## High-level design

### Scope and deployment

I'm building a dashboard for the 20 pending payouts in the ScanPay brief. The analyst can see every decision and its evidence, change a decision with a reason, compare counts and dollars with the preceding 20 days, and export `decisions.csv`.

| Component | Responsibility |
| --- | --- |
| React and TypeScript | Show the monitoring summary, payout queue, evidence, baseline comparisons, and override history. |
| FastAPI | Serve the UI and same-origin API for investigation, overrides, monitoring, and export. |
| Local processing command | Read the five CSVs, reconcile payout totals, use history to make decisions, and save the complete batch. |
| PostgreSQL | Store source records, known cases, original decisions, and override events. See the [database design](database-design.md). |
| Render and Neon | Render runs the application. A separate Neon database holds the synthetic reviewer data and overrides. |

~~~mermaid
flowchart TD
    Browser("Reviewer browser") -->|HTTPS| Render("Render web service")
    Render --> App("React dashboard and FastAPI")
    App -->|PostgreSQL over TLS| Neon("Neon synthetic database")
    Synthetic("Synthetic CSV files") --> Seed("Prepare synthetic batch")
    Seed --> Neon
    Actual("Five actual CSV files") --> Local("Private local run")
    Local --> LocalDB("Local PostgreSQL and assessment export")
    classDef step fill:#e3f2fd,stroke:#c5d4e2,color:#00549e
    class Browser,Render,App,Neon,Synthetic,Seed,Actual,Local,LocalDB step
~~~

I'll run the actual assessment data and generate the real export on my machine with local PostgreSQL. I'll share only column names, data types, and my code with AI tools. The hosted application uses independently fabricated inputs. The public reviewer link demonstrates the same decision and review workflows.

Render serves the built UI and FastAPI from one container on the supplied `PORT`. I'll use a Neon pooled TLS connection for app requests and a direct connection for migration and seeding. Connection strings and the cookie signing key stay in Render environment variables. I'll configure `/health` to check the database and synthetic batch before sharing the link. [Render web services](https://render.com/docs/web-services) · [Neon connection pooling](https://neon.com/docs/connect/connection-pooling)

### Payout rules

The decision cutoff is **13 November 2026 at 10:00 AM IST**. The 20 pending payouts were created on **12 November at 5:30 PM IST** and cover **10 November** collections. The source filenames and headers match the brief. The exact mappings are in the [database design](database-design.md#source-files).

I use the IST date of each payment and match it by team ID and the payout's inclusive coverage dates. Payouts run on weekdays at 5:30 PM IST, two business days after collection. Monday through Wednesday pay Wednesday through Friday, Thursday pays Monday, and Friday pays Tuesday. Weekend payments join Monday's collections for Wednesday. The payout total is `amount_usd + tip_usd` for captured payments only. Declined and refunded attempts stay available for risk analysis. I'll check the captured count and amount before publishing all 20 decisions together.

## Low-level design

### API endpoints

I'll use one dashboard with a summary, a payout queue, and a detail view. The browser can filter and sort the 20 payouts. Export always uses the full batch.

| Endpoint | What I'll use it for | Request and response |
| --- | --- | --- |
| GET `/api/dashboard` | Compare current exposure with history. | Returns batch creation time and covered dates, counts and cents for automatic approvals, analyst approvals, open review, and rejects, plus daily historical totals. |
| GET `/api/payouts` | Show every pending payout and its effective decision. | Returns payout ID, team ID and name, profession, payout amount, decision, short reason, and cluster ID for Reject. |
| GET `/api/payouts/{id}` | Explain why the payout received its decision. | Returns original and effective decisions, effective version, reasons, supporting records, covered attempts, applicable team and profession comparisons, any fraud cluster, and the override timeline. |
| POST `/api/payouts/{id}/overrides` | Change a payout decision and keep a record of the change. | Accepts `decision`, `reason`, `expected_version`, supporting `evidence`, and `fraud_cluster` for Reject. Returns HTTP 201 with the recorded change and new effective state. |
| GET `/api/exports/decisions.csv` | Download the entire decision batch. | Returns `decisions.csv` as `text/csv`, using effective decisions for the current review scope. |
| GET `/health` | Check hosted readiness. | Returns success when PostgreSQL is reachable and the published batch is available. |

I'll group historical payouts by IST creation date from **23 October through 11 November 2026**, using dispositions available by the decision cutoff. I'll show separate counts and dollars for `AUTO_APPROVED`, `MANUAL_APPROVED`, and `REJECTED`. A historical manual approval represents a completed review, so it does not measure an open review queue. If I show an average beside the current batch, I'll label it as per historical payout day and state the number of days used.

### How I'll learn normal behaviour

The submitted decisions will use **team and profession medians and interquartile ranges (IQR)**. This gives me the actual value, the historical range, and the records behind each unusual result.

- I'll start with payout amount, captured payment count, typical captured payment amount, and the decline rate across covered attempts.
- I'll use comparable approved historical coverage with decisions known by the cutoff and exclude fraud-implicated activity. For multi-day coverage, I'll compare comparable windows or calculate daily features separately. For profession comparisons, I'll exclude the current team from its peer group.
- I'll require sufficient observations and a material difference beyond the historical spread. If spread is zero, I'll apply the feature's minimum absolute difference. I'll record observed and baseline values, sample counts, threshold, and supporting IDs.
- If team history is sparse, I'll use a credible profession baseline. If the available data leaves material uncertainty, I'll send it for review and explain what the analyst should check.

**ML choice.** Isolation Forest is the optional model I'd test for combinations of unusual features. The brief has 20 days of history across 24 teams. I'll keep it disabled for the submitted decisions and use the explainable baseline comparisons and known-case rules. If I test it locally, I'll fit on earlier approved, comparable history and validate on later observations. I'd use a meaningful signal only to suggest Manual review, with specific features and supporting records. Reject always requires evidence of known fraud exposure. [scikit-learn Isolation Forest](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)

### How I'll recognize known fraud

I'll read historical `REJECTED` cases marked `FRAUD_CARD_RING`, `FRAUD_CARD_TESTING`, or `FRAUD_ACCOUNT_TAKEOVER` and inspect the historical reasons locally. Each supported case gets a stable cluster ID and references to the records actually implicated. If multiple cases qualify for one payout, I'll select the strongest supported link and break ties by cluster ID.

A captured payment on a specifically implicated card can establish exposure when the historical and current records support it. Shared phone, device, or routing and last four are leads that require corroboration. Blank ACH fingerprints contribute no link. `CARD_TESTING_VICTIM`, `OUTLIER_VERIFIED`, and `VOLUME_SPIKE_VERIFIED` are review context. I'll show the current record, the historical record, and what connects them.

~~~mermaid
flowchart TD
    Start("Check a pending payout") --> Known("Does the evidence establish known fraud exposure?")
    Known --> KYes([Yes])
    KYes --> Reject("Reject with cluster and supporting records")
    Known --> KNo([No])
    KNo --> Unusual("Is activity unusual or materially uncertain?")
    Unusual --> UYes([Yes])
    UYes --> Review("Manual review with a reason and supporting records")
    Unusual --> UNo([No])
    UNo --> Approve("Approve with a reason")
    classDef step fill:#e3f2fd,stroke:#c5d4e2,color:#00549e
    classDef check fill:#f1f7fc,stroke:#c5d4e2,stroke-dasharray:4 3,color:#00549e
    class Start,Reject,Review,Approve step
    class Known,KYes,KNo,Unusual,UYes,UNo check
~~~

### Overrides and export

I'll preserve the original outcome and append a new override when an analyst changes it. The reason is mandatory. Manual review requires supporting records and Reject requires an established link to the chosen cluster. If the analyst overrides a Reject to another outcome, the current cluster clears and the prior cluster remains in the audit history. A stale version returns HTTP 409. An invalid reason, decision, or evidence reference returns HTTP 422. On success I'll refresh the queue, detail, and monitoring. The [override transaction](database-design.md#effective-decisions-and-overrides) defines the write order.

The local app uses one review scope. For the hosted demo I'll use a server-signed, secure, HttpOnly, same-site cookie to keep reviewer overrides separate. I'll resolve the scope on the server, reject cross-origin writes, and keep session-specific responses out of shared caches.

The CSV header is `payout_id,team_id,decision,fraud_cluster,reasons,evidence`. Each pending payout appears once in source payout-ID order. The values are exactly `Approve`, `Manual review`, and `Reject`. Only Reject has a cluster. Each row has one or two plain-English sentences and the supporting transaction, payout, or team IDs.

### What I'll verify before hand-in

- I'll verify capture-only reconciliation, exact payout IDs, normal and unusual team or profession activity, confirmed fraud, weak links, and blank ACH fingerprints.
- I'll change a decision with a reason, inspect the audit event, verify the counts and dollars after the change, and confirm a separate demo session keeps its own view.
- I'll run the real files privately, verify the 20-row export and evidence, and prepare a walkthrough of the dashboard, a decision, an override, and the CSV. I'll share the Render and GitHub links and deliver the actual export privately.
