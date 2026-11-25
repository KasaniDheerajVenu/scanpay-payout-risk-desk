import csv
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from io import StringIO
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_auth import csrf_headers, make_settings
from test_decision_overrides import DEMO_ORIGIN, PAYMENT, body, known_run, override, signed_in
from test_evaluation import FIXTURES, INITIAL_CUTOFF, ORIGIN, PASSWORD, add_batch, incremental_files
from test_evaluation import evaluation_db as evaluation_db
from test_hybrid_evaluation import assessment_batch

from app.core.security import hash_password
from app.database.session import Database
from app.main import create_app
from app.ml.config import MLPolicy
from app.models import User
from app.models.values import FileType, UserRole
from app.repositories import risk_results as result_repository
from app.risk_engine.feature_schema import StatisticalPolicy
from app.services import evaluate_batch as executor
from app.services.evaluate_batch import hybrid_config
from app.services.evaluation_service import prepare_run


def read(client, resource, run_id, **params):
    return client.get(resource, params={"run_id": str(run_id), **params})


def start(client, batch_id, **fields):
    return client.post(
        "/api/evaluations",
        headers=csrf_headers(client),
        json={
            "batch_id": str(batch_id),
            **fields,
        },
    )


def exported(client, run_id, **params):
    response = read(client, "/api/exports/decisions.csv", run_id, **params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="decisions.csv"'
    reader = csv.DictReader(StringIO(response.text))
    assert reader.fieldnames == [
        "payout_id",
        "team_id",
        "decision",
        "fraud_cluster",
        "reasons",
        "evidence",
    ]
    return list(reader)


def upload_fraud_fixture(client, tmp_path, *, reconstruct=None):
    paths = {}
    for kind in FileType:
        data = (FIXTURES / "initial" / f"{kind}.csv").read_bytes()
        if kind == FileType.HISTORY_PAYOUTS:
            data = data.replace(b"Fabricated fixture case.", b"implicated transaction: 000102")
            if reconstruct == "ring":
                data = (
                    data.replace(b",2,20.00,", b",3,21.00,")
                    .replace(b",1,28.00,", b",2,29.00,")
                    .replace(
                        b"implicated transaction: 000102",
                        b"The same 2 cards paid Imaginary Plumbing Workshop 1 and "
                        b"Imaginary Locksmith Workshop 2.",
                    )
                )
            elif reconstruct == "testing":
                data = (
                    data.replace(b",2,20.00,", b",2,6.01,")
                    .replace(b",1,28.00,", b",1,900.00,")
                    .replace(
                        b"FRAUD_CARD_RING,implicated transaction: 000102",
                        b"FRAUD_CARD_TESTING,1 foreign card ran $2-4 test charges at Imaginary "
                        b"Plumbing Workshop 1 and Fictional Repair Studio "
                        b"around 11:06 AM IST then was charged $800+ here.",
                    )
                )
        if kind == FileType.HISTORY_TRANSACTIONS and reconstruct == "ring":
            data = data.replace(b"DEMO-CARD-000101", b"FIXTURE-CARD").rstrip() + (
                b"\n000110,0001,2025-02-10 14:21,1.00,0,card,SYNTH-SECOND,US,captured,Test,00420\n"
                b"000111,0002,2025-02-10 11:21,1.00,0,card,SYNTH-SECOND,US,captured,Test,00420\n"
            )
        if kind == FileType.HISTORY_TRANSACTIONS and reconstruct == "testing":
            data = (
                data.replace(b"14:20:00,16.99", b"11:05:00,3.00")
                .replace(b"DEMO-CARD-000101", b"FIXTURE-CARD")
                .replace(b",28.00,", b",900.00,")
                .replace(b"FIXTURE-CARD,US", b"FIXTURE-CARD,CA")
                .rstrip()
                + b"\n000110,0008,2025-02-10 11:06,3.00,0,card,"
                b"FIXTURE-CARD,CA,declined,Test,00420\n"
            )
        if kind == FileType.TEAMS and reconstruct == "testing":
            data = data.rstrip() + (
                b"\n0008,Fictional Repair Studio,Plumbing,Fictional Owner,0005550008,"
                b"8 Synthetic Lane,Example Harbor,MA,2024-06-01,active,,000000019,0008,\n"
            )
        if kind == FileType.LATEST_TRANSACTIONS:
            data = data.replace(b"DEMO-CARD-000301", b"FIXTURE-CARD")
        paths[kind] = tmp_path / f"{kind}.csv"
        paths[kind].write_bytes(data)
    with ExitStack() as opened:
        files = {
            str(kind): (path.name, opened.enter_context(path.open("rb")), "text/csv")
            for kind, path in paths.items()
        }
        response = client.post(
            "/api/datasets/upload",
            headers=csrf_headers(client),
            data={"mode": "INITIAL", "cutoff": INITIAL_CUTOFF},
            files=files,
        )
    assert response.status_code == 201, response.text
    for path in paths.values():
        path.unlink()
    assert not list(tmp_path.iterdir())
    return response.json()["batch_id"]


@pytest.mark.parametrize("scenario", ["ring", "testing"])
def test_natural_language_case_published_with_cross_team_witness(evaluation_db, tmp_path, scenario):
    _, settings, admin, _ = evaluation_db
    with ExitStack() as stack:
        operator = signed_in(stack, settings, admin.username)
        batch_id = upload_fraud_fixture(operator, tmp_path, reconstruct=scenario)
        response = start(operator, batch_id)
        assert response.status_code == 201, response.json()
        run_id = response.json()["run_id"]
        detail = read(operator, "/api/payouts/000401", run_id).json()
        assert detail["payout"]["effective_decision"] == "REJECT"
        rows = {row["payout_id"]: row for row in exported(operator, run_id)}
        assert rows["000401"]["decision"] == "Reject"
        references = set(rows["000401"]["evidence"].split(";"))
        assert {"000101", "000102", "000301"} <= references
        if scenario == "testing":
            assert "000110" in references
        assert rows["000402"]["decision"] != "Reject"
        replay = start(operator, batch_id)
        assert replay.status_code == 200 and replay.json()["run_id"] == run_id


def test_synthetic_workflow_override_updates_investigation_queue_dashboard_and_csv(
    evaluation_db,
    tmp_path,
    monkeypatch,
):
    database, settings, admin, analyst = evaluation_db
    with ExitStack() as stack:
        operator = signed_in(stack, settings, admin.username)
        reviewer = signed_in(stack, settings, analyst.username)
        batch_id = upload_fraud_fixture(operator, tmp_path)
        assert start(operator, batch_id, config_version="unknown").status_code == 422
        assert start(reviewer, batch_id).status_code == 403
        assert operator.post("/api/evaluations", json={"batch_id": batch_id}).status_code == 403
        response = start(operator, batch_id, config_version="hybrid-v2")
        assert response.status_code == 201, response.json()
        run = response.json()
        run_id = run["run_id"]
        assert run["status"] == "COMPLETED" and run["summary"]["payout_count"] == 2
        replay = start(operator, batch_id)
        assert replay.status_code == 200 and replay.json()["run_id"] == run_id
        assert reviewer.get("/api/evaluations", params={"batch_id": batch_id}).json() == [run]
        assert reviewer.get(f"/api/evaluations/{run_id}").json() == run
        assert reviewer.get(f"/api/evaluations/{uuid4()}").status_code == 404
        assert reviewer.get("/api/dashboard").status_code == 422

        def no_training(*args, **kwargs):
            pytest.fail("read routes must only use persisted results")

        analyze = executor.analyze_evaluation
        monkeypatch.setattr(executor, "analyze_evaluation", no_training)
        dashboard = read(reviewer, "/api/dashboard", run_id).json()
        assert dashboard["total"] == {"count": 2, "cents": 3625}
        assert dashboard["categories"]["rejected"] == {"count": 1, "cents": 3125}
        assert dashboard["categories"]["open_manual_review"] == {"count": 1, "cents": 500}
        frozen = dashboard["historical"]
        assert frozen["calendar_days"] == len(frozen["days"]) == 20
        assert frozen["totals"]["MANUAL_APPROVED"] == {"count": 1, "cents": 2000}
        assert any(
            day["totals"] is None
            for day in frozen["days"]
            if day["coverage"] == "unknown_source_coverage"
        )
        assert reviewer.get("/api/models").json() == []

        detail = read(reviewer, "/api/payouts/000401", run_id).json()
        assert detail["payout"]["captured_count"] == 2
        assert {txn["txn_id"] for txn in detail["transactions"]} == {
            "000301",
            "000302",
            "000303",
            "000304",
        }
        assert detail["payout"]["effective_decision"] == "REJECT"
        assert detail["statistics"][0]["team"]["baseline"]["sample_count"] >= 0
        assert detail["statistics"][0]["profession"]["baseline"]["reliability"]
        assert detail["fraud"]["primary"]["paths"] and detail["ml"]["status"]
        for private in (
            "FIXTURE-CARD",
            "Fabricated cardholder",
            "implicated transaction: 000102",
            "bank_routing",
            "owner_phone",
            "customer_name",
        ):
            assert private not in str(detail)
        clusters = read(reviewer, "/api/fraud-clusters", run_id).json()
        assert clusters[0]["reason_codes"] == ["FRAUD_CARD_RING"]
        assert clusters[0]["associated_payouts"][0]["paths"]
        assert exported(reviewer, run_id)[0]["fraud_cluster"] == clusters[0]["cluster_id"]
        assert exported(reviewer, run_id)[0]["evidence"] == "0001;000102;0002;000202;000301;000401"

        reason = '=1+1, "checked"\nReviewed the synthetic invoice.'
        changed = override(reviewer, body(run_id, 1, reason=reason, evidence=[PAYMENT]))
        assert changed.status_code == 201
        page = read(
            reviewer, "/api/payouts", run_id, decision="APPROVE", search="0001", limit=1
        ).json()
        assert page["total"] == 1 and page["items"][0]["original_decision"] == "REJECT"
        assert page["items"][0]["decision_source"] == "HUMAN"
        ranked = read(
            reviewer, "/api/payouts", run_id, sort="amount", direction="desc", limit=1, offset=1
        ).json()
        assert ranked["total"] == 2 and ranked["items"][0]["payout_id"] == "000402"
        assert read(reviewer, "/api/payouts", run_id, search="%").json()["total"] == 0
        assert read(reviewer, "/api/payouts", run_id, limit=101).status_code == 422
        decided = read(reviewer, "/api/payouts/000401", run_id).json()["decisions"]
        assert decided == read(reviewer, "/api/payouts/000401/history", run_id).json()
        assert decided["effective"]["effective_version"] == 2
        after = read(reviewer, "/api/dashboard", run_id).json()
        assert after["total"] == dashboard["total"]
        assert after["categories"]["analyst_approved"] == {"count": 1, "cents": 3125}
        assert sum(item["cents"] for item in after["categories"].values()) == 3625
        csv_rows = exported(reviewer, run_id, decision="REJECT", limit=1)
        assert len(csv_rows) == 2 and csv_rows[0]["decision"] == "Approve"
        assert csv_rows[0]["fraud_cluster"] == "" and csv_rows[0]["reasons"] == "'" + reason
        assert csv_rows[1]["decision"] == "Manual review"

        files = incremental_files()
        header = (FIXTURES / "initial" / "history_payouts.csv").read_bytes().splitlines()[0]
        files[FileType.HISTORY_PAYOUTS] = header + (
            b"\nADDED-HISTORY,0001,2025-02-13 17:30:00,2025-02-11,2025-02-11,1,3.00,"
            b"MANUAL_APPROVED,Fixture Reviewer,2025-02-13 18:00:00,,\n"
        )
        tx_header = (FIXTURES / "initial" / "history_transactions.csv").read_bytes().splitlines()[0]
        files[FileType.HISTORY_TRANSACTIONS] = tx_header + (
            b"\nADDED-HISTORY-TX,0001,2025-02-11 13:00:00,3.00,0.00,"
            b"ach,,,captured,Fictional,00420\n"
        )
        files[FileType.LATEST_TRANSACTIONS] += (
            b"LATE-FOR-OLD-PAYOUT,0001,2025-02-17 13:00:00,10.75,0,ach,,,captured,Fictional,00420\n"
        )
        add_batch(evaluation_db, files)
        assert read(reviewer, "/api/dashboard", run_id).json()["historical"] == frozen
        assert (
            read(reviewer, "/api/payouts/000401", run_id).json()["transactions"]
            == detail["transactions"]
        )
        assert exported(reviewer, run_id) == csv_rows
        queued = prepare_run(
            database,
            admin,
            UUID(batch_id),
            hybrid_config(MLPolicy(n_estimators=50), StatisticalPolicy()),
        ).run
        queued_detail = reviewer.get(f"/api/evaluations/{queued.run_id}").json()
        assert queued_detail["status"] == "QUEUED" and queued_detail["summary"] is None
        assert read(reviewer, "/api/dashboard", queued.run_id).status_code == 404
        monkeypatch.setattr(executor, "analyze_evaluation", analyze)
        re_evaluated = start(operator, batch_id)
        assert re_evaluated.status_code == 201
        newer = re_evaluated.json()["run_id"]
        assert newer != run_id
        assert exported(reviewer, newer)[0]["decision"] == "Reject"
        assert read(reviewer, "/api/dashboard", newer).json()["historical"]["totals"][
            "MANUAL_APPROVED"
        ] == {"count": 2, "cents": 2300}
        assert exported(reviewer, run_id) == csv_rows


def test_assessment_export_and_model_metadata_from_published_postgresql_results(
    evaluation_db, tmp_path
):
    _, settings, admin, _ = evaluation_db
    batch = assessment_batch(evaluation_db, tmp_path)
    with ExitStack() as stack:
        client = signed_in(stack, settings, admin.username)
        response = start(client, batch.batch_id)
        assert response.status_code == 201, response.json()
        run = response.json()
        rows = exported(client, run["run_id"])
        ids = [row["payout_id"] for row in rows]
        assert len(ids) == len(set(ids)) == 20 and ids == sorted(ids)
        assert all(
            row["decision"] == "Manual review" and row["fraud_cluster"] == "" for row in rows
        )
        assert run["summary"]["total_cents"] == 247010
        dashboard = read(client, "/api/dashboard", run["run_id"]).json()
        assert dashboard["total"]["cents"] == 247010
        models = client.get("/api/models", params={"limit": 1}).json()
        assert models == [run["model"]]
        assert models[0]["algorithm"] == "isolation_forest"
        assert models[0]["metrics"]["validation_scored"] > 0
        assert models[0]["quality_gates"] and models[0]["anomaly_threshold"] is not None
        assert not {"training_fingerprint", "scope_key", "config"} & models[0].keys()


def test_export_keeps_snapshot_started_before_concurrent_override(evaluation_db, monkeypatch):
    _, settings, admin, analyst = evaluation_db
    _, run, _ = known_run(evaluation_db)
    frozen, changed = Event(), Event()
    query = result_repository.result_amounts

    def paused(*args):
        frozen.set()
        assert changed.wait(15)
        return query(*args)

    with ExitStack() as stack, ThreadPoolExecutor(max_workers=1) as pool:
        first = signed_in(stack, settings, admin.username)
        second = signed_in(stack, settings, analyst.username)
        with monkeypatch.context() as patch:
            patch.setattr(result_repository, "result_amounts", paused)
            future = pool.submit(exported, first, run.run_id)
            try:
                assert frozen.wait(15)
                assert override(second, body(run.run_id, 1)).status_code == 201
            finally:
                changed.set()
            assert [row["decision"] for row in future.result(25)] == ["Reject", "Manual review"]
        assert [row["decision"] for row in exported(first, run.run_id)] == [
            "Approve",
            "Manual review",
        ]


def test_demo_results_and_exports_respect_review_scope_and_permissions(evaluation_db):
    database, settings, admin, analyst = evaluation_db
    with database.session() as session:
        session.add(
            User(
                username="risk-viewer",
                display_name="Synthetic Viewer",
                role=UserRole.VIEWER,
                password_hash=hash_password(PASSWORD),
            )
        )
        session.commit()
    demo_settings = make_settings(settings.database_url, mode="demo")
    demo = Database(demo_settings)
    try:
        batch, run, _ = known_run(
            (
                demo,
                demo_settings,
                replace(admin, scope_key=f"demo:{admin.user_id}"),
                replace(analyst, scope_key=f"demo:{analyst.user_id}"),
            )
        )
    finally:
        demo.close()
    with ExitStack() as stack:
        first = signed_in(stack, demo_settings, admin.username, DEMO_ORIGIN)
        second = signed_in(stack, demo_settings, analyst.username, DEMO_ORIGIN)
        viewer = signed_in(stack, demo_settings, "risk-viewer", DEMO_ORIGIN)
        assert read(viewer, "/api/dashboard", run.run_id).status_code == 200
        assert read(viewer, "/api/exports/decisions.csv", run.run_id).status_code == 403
        assert first.get("/api/evaluations").json()[0]["batch_id"] == str(batch.batch_id)
        assert override(first, body(run.run_id, 1), origin=DEMO_ORIGIN).status_code == 201
        assert (
            read(first, "/api/payouts/000401", run.run_id).json()["payout"]["effective_version"]
            == 2
        )
        assert (
            read(second, "/api/payouts/000401", run.run_id).json()["payout"]["effective_version"]
            == 1
        )
        assert exported(first, run.run_id)[0]["decision"] == "Approve"
        assert exported(second, run.run_id)[0]["decision"] == "Reject"
        local = signed_in(stack, settings, admin.username)
        assert local.get("/api/evaluations").json() == []
        assert read(local, "/api/dashboard", run.run_id).status_code == 404
        anonymous = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        assert read(anonymous, "/api/exports/decisions.csv", run.run_id).status_code == 401
