import csv
from contextlib import ExitStack
from datetime import date, timedelta
from io import StringIO

from test_auth import csrf_headers
from test_decision_overrides import signed_in
from test_evaluation import FIXTURES, INITIAL_CUTOFF
from test_evaluation import evaluation_db as evaluation_db
from test_risk_operations import exported, read, start

from app.models.values import FileType

NORMAL = (100, 100)
SCENARIOS = (
    ("routine", "Plumbing", NORMAL, "card", "Approve", ""),
    ("ach", "HVAC", (200,), "ach", "Manual review", "invoice or job record"),
    ("job", "Locksmith", (200,), "card", "Manual review", "invoice or job record"),
    ("volume", "Handyman", (100,) * 6, "card", "Manual review", "typical ticket size"),
)


def csv_upload():
    rows = {kind: [] for kind in FileType}
    action = date(2025, 2, 17)
    for team, profession, current, method, _, _ in SCENARIOS:
        rows[FileType.TEAMS].append(
            [
                team,
                f"Synthetic {team}",
                profession,
                "Synthetic Owner",
                "0005550111",
                "Synthetic Lane",
                "Example Harbor",
                "MA",
                "2024-01-01",
                "active",
                "",
                "000000019",
                "0001",
                "",
            ]
        )
        for week in range(8, -1, -1):
            day = action - timedelta(weeks=week)
            settlement = day + timedelta(days=2)
            key = f"{team}-{week}"
            reason, note, amounts = "", "", NORMAL
            if week == 3:
                reason, note, amounts = (
                    "OUTLIER_VERIFIED",
                    "Invoice checked for a large job.",
                    (900,),
                )
            elif week == 2:
                reason = "VOLUME_SPIKE_VERIFIED"
                note, amounts = "Analyst verified an unusually busy collection day.", (100,) * 6
            elif week == 1:
                reason = "CARD_TESTING_VICTIM"
                note = "Declined probes were excluded. Remaining activity was verified."
            if week == 0:
                amounts = current
            txn_kind = FileType.HISTORY_TRANSACTIONS if week else FileType.LATEST_TRANSACTIONS
            for i, amount in enumerate(amounts):
                txn = f"{key}-{i}"
                rows[txn_kind].append(
                    [
                        txn,
                        team,
                        f"{day} 11:{i:02d}",
                        amount,
                        0,
                        method,
                        "" if method == "ach" else f"card-{txn}",
                        "" if method == "ach" else "US",
                        "captured",
                        "Synthetic",
                        "00420",
                    ]
                )
            if reason == "CARD_TESTING_VICTIM" and method != "ach":
                rows[txn_kind].append(
                    [
                        f"{key}-probe",
                        team,
                        f"{day} 12:00",
                        2,
                        0,
                        "card",
                        f"probe-{key}",
                        "US",
                        "declined",
                        "Synthetic",
                        "00420",
                    ]
                )
            payout = [key, team, f"{settlement} 17:30", day, day, len(amounts), sum(amounts)]
            if week:
                rows[FileType.HISTORY_PAYOUTS].append(
                    payout
                    + [
                        "MANUAL_APPROVED" if reason else "AUTO_APPROVED",
                        "Synthetic Analyst",
                        f"{settlement} 18:00",
                        reason,
                        note,
                    ]
                )
            else:
                rows[FileType.PENDING_PAYOUTS].append([*payout, "PENDING_DECISION"])

    upload = {}
    for kind, records in rows.items():
        header = (FIXTURES / "initial" / f"{kind}.csv").read_text().splitlines()[0]
        data = StringIO(newline="")
        csv.writer(data).writerows([header.split(","), *records])
        upload[str(kind)] = (f"{kind}.csv", data.getvalue().encode(), "text/csv")
    return upload


def test_verified_exceptions_keep_independent_routine_baselines_and_published_decisions(
    evaluation_db,
):
    _, settings, admin, _ = evaluation_db
    with ExitStack() as stack:
        client = signed_in(stack, settings, admin.username)
        upload = client.post(
            "/api/datasets/upload",
            headers=csrf_headers(client),
            data={"mode": "INITIAL", "cutoff": INITIAL_CUTOFF},
            files=csv_upload(),
        )
        assert upload.status_code == 201, upload.text
        completed = start(client, upload.json()["batch_id"])
        assert completed.status_code == 201, completed.text
        run_id = completed.json()["run_id"]
        csv_rows = {r["payout_id"]: r for r in exported(client, run_id)}
        assert len(csv_rows) == 4
        for team, _, amounts, _, label, reason in SCENARIOS:
            key = f"{team}-0"
            row = csv_rows[key]
            detail = read(client, f"/api/payouts/{key}", run_id).json()
            assert (row["decision"], row["fraud_cluster"]) == (label, "")
            assert reason in row["reasons"]
            assert detail["payout"]["captured_count"] == len(amounts)
            count = next(
                p["team"] for p in detail["statistics"] if p["team"]["feature"] == "captured_count"
            )
            assert set(count["baseline"]["payout_ids"]) == {
                f"{team}-{week}" for week in range(4, 9)
            }
        dashboard = read(client, "/api/dashboard", run_id).json()
        assert dashboard["total"] == {"count": 4, "cents": 120_000}
        assert dashboard["categories"]["open_manual_review"] == {"count": 3, "cents": 100_000}
