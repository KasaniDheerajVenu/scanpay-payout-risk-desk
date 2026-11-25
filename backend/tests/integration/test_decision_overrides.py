from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_auth import DEMO_ORIGIN, csrf_headers, login, make_settings
from test_evaluation import ORIGIN, PASSWORD
from test_evaluation import evaluation_db as evaluation_db
from test_hybrid_evaluation import seed_fraud

from app.core.security import hash_password
from app.database.session import Database
from app.main import create_app
from app.ml.config import MLPolicy
from app.models import FraudCluster, Payout, PayoutDecision, PayoutDecisionHistory, User
from app.models.values import UserRole
from app.repositories import payout_decisions
from app.risk_engine.feature_schema import StatisticalPolicy
from app.services.evaluate_batch import evaluate_batch, hybrid_config
from app.services.evaluation_input_service import capture_inputs
from app.services.evaluation_manifest import fingerprint
from app.services.evaluation_service import prepare_run

PAYMENT = {"type": "transaction", "id": "000301"}
CASE_EVIDENCE = [
    PAYMENT,
    {"type": "transaction", "id": "000102"},
    {"type": "payout", "id": "000202"},
]


def body(run_id, version, decision="APPROVE", **kwargs):
    return {
        "run_id": str(run_id),
        "expected_version": version,
        "decision": decision,
        "reason": "Reconcile the cited synthetic payments against the supporting invoice.",
        **kwargs,
    }


def override(client, data, *, payout_id="000401", origin=ORIGIN):
    return client.post(
        f"/api/payouts/{payout_id}/overrides",
        json=data,
        headers=csrf_headers(client, origin),
    )


def timeline(client, run_id, payout_id="000401"):
    response = client.get(f"/api/payouts/{payout_id}/history", params={"run_id": str(run_id)})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


def signed_in(stack, settings, username, origin=ORIGIN):
    client = stack.enter_context(TestClient(create_app(settings), base_url=origin))
    assert login(client, username, PASSWORD, origin=origin).status_code == 200
    return client


def known_run(context):
    database, _, admin, _ = context
    batch = seed_fraud(context)
    run = evaluate_batch(database, admin, batch.batch_id).run
    with database.session() as session:
        cluster_id = session.scalar(
            select(FraudCluster.cluster_id).where(FraudCluster.run_id == run.run_id)
        )
    return batch, run, cluster_id


def automated_and_history(database, run_id):
    with database.session() as session:
        original = session.scalar(
            select(PayoutDecision).where(
                PayoutDecision.run_id == run_id,
                PayoutDecision.payout_id == "000401",
            )
        )
        events = session.scalars(
            select(PayoutDecisionHistory)
            .where(
                PayoutDecisionHistory.decision_id == original.decision_id,
            )
            .order_by(PayoutDecisionHistory.version)
        ).all()
        return (
            {
                column.name: getattr(original, column.name)
                for column in PayoutDecision.__table__.columns
            },
            [
                {
                    column.name: getattr(event, column.name)
                    for column in PayoutDecisionHistory.__table__.columns
                }
                for event in events
            ],
        )


def test_local_transitions_append_and_persist_without_changing_prediction(evaluation_db):
    database, settings, admin, analyst = evaluation_db
    batch, run, cluster_id = known_run(evaluation_db)
    evaluation_digest = fingerprint(capture_inputs(database, admin, batch.batch_id))
    automated, history = automated_and_history(database, run.run_id)
    assert not history and automated["decision"] == "REJECT"
    with ExitStack() as stack:
        administrator = signed_in(stack, settings, admin.username)
        reviewer = signed_in(stack, settings, analyst.username)
        assert timeline(reviewer, run.run_id)["history"][0]["status"] == "CURRENT"
        previous = "REJECT"
        for expected, decision in enumerate(
            ("APPROVE", "MANUAL_REVIEW", "REJECT", "MANUAL_REVIEW", "APPROVE", "REJECT"), start=1
        ):
            kwargs = (
                {"cluster_id": str(cluster_id), "evidence": CASE_EVIDENCE}
                if decision == "REJECT"
                else {"evidence": [PAYMENT]}
                if decision == "MANUAL_REVIEW"
                else {}
            )
            client, actor = (administrator, admin) if expected % 2 else (reviewer, analyst)
            response = override(client, body(run.run_id, expected, decision, **kwargs))
            assert response.status_code == 201, response.json()
            assert response.headers["cache-control"] == "private, no-store"
            effective = response.json()
            assert effective["original_decision"] == "REJECT"
            assert effective["effective_decision"] == decision
            assert effective["effective_version"] == expected + 1
            assert effective["decision_source"] == "HUMAN"
            assert effective["cluster_id"] == kwargs.get("cluster_id")
            assert effective["actor"] == {
                "user_id": str(actor.user_id),
                "display_name": actor.display_name,
            }
            other = reviewer if expected % 2 else administrator
            visible = timeline(other, run.run_id)
            assert visible["effective"] == effective
            assert [event["version"] for event in visible["history"]] == list(
                range(1, expected + 2)
            )
            assert [event["status"] for event in visible["history"]] == [
                "SUPERSEDED"
            ] * expected + ["CURRENT"]
            assert visible["history"][-1]["previous_decision"] == previous
            assert visible["history"][-1]["new_decision"] == decision
            saved, events = automated_and_history(database, run.run_id)
            assert saved == automated and events[:-1] == history
            assert events[-1]["scope_key"] == "local"
            history, previous = events, decision
        before_restart = visible
    with ExitStack() as stack:
        after_restart = signed_in(stack, settings, analyst.username)
        assert timeline(after_restart, run.run_id) == before_restart
        newer = evaluate_batch(
            database, admin, batch.batch_id, ml_policy=MLPolicy(n_estimators=50)
        ).run
        assert newer.run_id != run.run_id
        fresh = timeline(after_restart, newer.run_id)
        assert fresh["effective"]["effective_version"] == 1
        assert timeline(after_restart, run.run_id) == before_restart
    captured = capture_inputs(database, admin, batch.batch_id)
    assert fingerprint(captured) == evaluation_digest
    assert "000401" not in {row.payout.payout_id for row in captured.normal_candidates}
    with database.session() as session:
        assert session.get(Payout, "000401").historical_decision is None


def test_override_failed_authorization_validation_and_evidence(evaluation_db):
    database, settings, admin, analyst = evaluation_db
    batch, run, cluster_id = known_run(evaluation_db)
    other_run = evaluate_batch(
        database, admin, batch.batch_id, ml_policy=MLPolicy(n_estimators=50)
    ).run
    queued = prepare_run(
        database,
        admin,
        batch.batch_id,
        hybrid_config(
            MLPolicy(n_estimators=51),
            StatisticalPolicy(),
        ),
    ).run
    with database.session() as session:
        other_cluster_id = session.scalar(
            select(FraudCluster.cluster_id).where(FraudCluster.run_id == other_run.run_id)
        )
        session.add(
            User(
                username="phase10-viewer",
                display_name="Synthetic Viewer",
                role=UserRole.VIEWER,
                password_hash=hash_password(PASSWORD),
            )
        )
        session.commit()
    with ExitStack() as stack:
        unsigned = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        url = "/api/payouts/000401/overrides"
        initial = body(run.run_id, 1)
        assert unsigned.post(url, json=initial).status_code == 401
        assert (
            unsigned.get(
                "/api/payouts/000401/history", params={"run_id": str(run.run_id)}
            ).status_code
            == 401
        )
        viewer = signed_in(stack, settings, "phase10-viewer")
        assert override(viewer, initial).status_code == 403
        assert timeline(viewer, run.run_id)["effective"]["effective_version"] == 1
        reviewer = signed_in(stack, settings, analyst.username)
        assert reviewer.post(url, json=initial).status_code == 403
        forged_origin = csrf_headers(reviewer)
        forged_origin["Origin"] = "https://cross-origin.invalid"
        assert reviewer.post(url, json=initial, headers=forged_origin).status_code == 403
        for forged in (
            {"changed_by": str(admin.user_id)},
            {"scope_key": "demo:forged"},
            {"created_at": "2025-01-01T00:00:00Z"},
            {"reason": "  "},
        ):
            response = override(reviewer, initial | forged)
            assert response.status_code == 422 and "forged" not in response.text
        assert override(reviewer, body(queued.run_id, 1)).status_code == 404
        assert override(reviewer, body(uuid4(), 1)).status_code == 404
        assert override(reviewer, initial, payout_id="not-in-this-run").status_code == 404
        assert reviewer.get("/api/payouts/000401/history").status_code == 422
        assert override(reviewer, initial).status_code == 201
        assert override(reviewer, initial).status_code == 409
        invalid = [
            (body(run.run_id, 2), "unchanged_decision"),
            (
                body(run.run_id, 2, "MANUAL_REVIEW", cluster_id=str(cluster_id)),
                "invalid_override_cluster",
            ),
            (body(run.run_id, 2, "REJECT", evidence=CASE_EVIDENCE), "invalid_override_cluster"),
            (
                body(
                    run.run_id,
                    2,
                    "REJECT",
                    cluster_id=str(other_cluster_id),
                    evidence=CASE_EVIDENCE,
                ),
                "invalid_override_cluster",
            ),
            (
                body(run.run_id, 2, "REJECT", cluster_id=str(cluster_id), evidence=[PAYMENT]),
                "override_exposure_not_established",
            ),
            (
                body(run.run_id, 2, "MANUAL_REVIEW", evidence=[PAYMENT, PAYMENT]),
                "invalid_override_evidence",
            ),
            (
                body(
                    run.run_id,
                    2,
                    "MANUAL_REVIEW",
                    evidence=[
                        {"type": "transaction", "id": "000305"},
                    ],
                ),
                "invalid_override_evidence",
            ),
            (body(run.run_id, 2, "MANUAL_REVIEW"), "review_evidence_required"),
            (
                body(
                    run.run_id,
                    2,
                    "MANUAL_REVIEW",
                    evidence=[
                        {"type": "transaction", "id": "nonexistent"},
                    ],
                ),
                "invalid_override_evidence",
            ),
        ]
        for data, code in invalid:
            response = override(reviewer, data)
            assert response.status_code == 422, (code, response.json())
            assert response.json()["error"]["code"] == code
        unrelated = override(
            reviewer, body(run.run_id, 1, "REJECT", cluster_id=str(cluster_id)), payout_id="000402"
        )
        assert unrelated.status_code == 422
        assert unrelated.json()["error"]["code"] == "override_exposure_not_established"
        assert timeline(reviewer, run.run_id)["effective"]["effective_version"] == 2


def test_concurrent_matching_version_succeeds_once_and_failed_append_rolls_back(
    evaluation_db, monkeypatch
):
    database, settings, admin, analyst = evaluation_db
    _, run, cluster_id = known_run(evaluation_db)
    with ExitStack() as stack:
        clients = [signed_in(stack, settings, actor.username) for actor in (admin, analyst)]
        headers = [csrf_headers(client) for client in clients]
        barrier = Barrier(2)

        def send(index):
            barrier.wait(timeout=15)
            return (
                clients[index]
                .post(
                    "/api/payouts/000401/overrides",
                    headers=headers[index],
                    json=body(
                        run.run_id,
                        1,
                        "APPROVE" if index == 0 else "MANUAL_REVIEW",
                        evidence=[PAYMENT],
                    ),
                )
                .status_code
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(send, (0, 1))) == [201, 409]
        before = automated_and_history(database, run.run_id)
        append = payout_decisions.append_override

        def fail(session, **values):
            append(session, **values)
            raise RuntimeError("synthetic persistence detail")

        with monkeypatch.context() as patch:
            patch.setattr(payout_decisions, "append_override", fail)
            response = override(
                clients[0],
                body(
                    run.run_id,
                    2,
                    "REJECT",
                    cluster_id=str(cluster_id),
                    evidence=CASE_EVIDENCE,
                ),
            )
            assert (
                response.status_code == 500 and "synthetic persistence detail" not in response.text
            )
        assert automated_and_history(database, run.run_id) == before
        assert (
            override(
                clients[0],
                body(
                    run.run_id,
                    2,
                    "REJECT",
                    cluster_id=str(cluster_id),
                    evidence=CASE_EVIDENCE,
                ),
            ).json()["effective_version"]
            == 3
        )


def test_hosted_review_history_is_account_scoped_and_local_read_is_hidden(evaluation_db):
    _, local_settings, admin, analyst = evaluation_db
    demo_settings = make_settings(local_settings.database_url, mode="demo")
    demo = Database(demo_settings)
    try:
        _, run, _ = known_run(
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
        assert override(first, body(run.run_id, 1), origin=DEMO_ORIGIN).status_code == 201
        assert timeline(second, run.run_id)["effective"]["effective_version"] == 1
        own = override(
            second,
            body(
                run.run_id,
                1,
                "MANUAL_REVIEW",
                evidence=[PAYMENT],
            ),
            origin=DEMO_ORIGIN,
        )
        assert own.status_code == 201 and own.json()["effective_version"] == 2
        approved, reviewed = timeline(first, run.run_id), timeline(second, run.run_id)
        assert approved["effective"]["effective_decision"] == "APPROVE"
        assert reviewed["effective"]["effective_decision"] == "MANUAL_REVIEW"
        assert [event["actor"]["user_id"] for event in approved["history"][1:]] == [
            str(admin.user_id)
        ]
        assert [event["actor"]["user_id"] for event in reviewed["history"][1:]] == [
            str(analyst.user_id)
        ]
        local = signed_in(stack, local_settings, admin.username)
        assert (
            local.get("/api/payouts/000401/history", params={"run_id": str(run.run_id)}).status_code
            == 404
        )
