from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, insert, inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql import Executable

from app.api.dependencies import get_session
from app.core.config import Settings
from app.database.session import Database
from app.main import create_app
from app.models import (
    Base,
    DatasetBatch,
    EvaluationRun,
    FraudCluster,
    ModelVersion,
    Payout,
    PayoutDecision,
    PayoutDecisionHistory,
    Team,
    Transaction,
    UploadedFile,
    User,
)

CUTOFF = datetime(2026, 11, 13, 4, 30, tzinfo=UTC)
CREATED = datetime(2026, 11, 12, 12, tzinfo=UTC)
EVIDENCE = [{"type": "transaction", "id": "001"}]


@dataclass
class Records:
    user: User
    batch: DatasetBatch
    pending: Payout
    history: Payout
    run: EvaluationRun
    cluster: FraudCluster
    decision: PayoutDecision


@pytest.fixture
def records(session: Session) -> Records:
    user = User(
        username="fictional-analyst",
        password_hash="fixture-only",
        display_name="Demo",
        role="ANALYST",
    )
    batch = DatasetBatch(
        uploader=user,
        scope_key="shared",
        mode="INITIAL",
        cutoff_at=CUTOFF,
        status="READY",
        input_digest="fixture-batch",
        imported_at=CUTOFF,
    )
    team = Team(
        team_id="0001",
        team_name="Invented Plumbing",
        profession="Plumbing",
        owner_name="Fictional Owner",
        owner_phone="0000000000",
        business_address="1 Invented Lane",
        city="Example",
        state="ZZ",
        onboarded_on=date(2024, 1, 1),
        status="active",
        payout_bank_routing="000000001",
        payout_bank_last4="0002",
        first_import_batch=batch,
    )
    payment = Transaction(
        txn_id="001",
        team=team,
        first_import_batch=batch,
        created_at=datetime(2026, 11, 9, 20, tzinfo=UTC),
        collection_date_ist=date(2026, 11, 10),
        amount_cents=3_000_000_000,
        tip_cents=25,
        payment_method="ach",
        status="captured",
        customer_name="Fictional Customer",
        customer_zip="00004",
    )
    pending = Payout(
        payout_id="005",
        team=team,
        first_import_batch=batch,
        first_source_kind="PENDING",
        source_status="PENDING_DECISION",
        created_at=CREATED,
        created_date_ist=date(2026, 11, 12),
        covers_from=date(2026, 11, 10),
        covers_to=date(2026, 11, 10),
        txn_count=1,
        payout_amount_cents=3_000_000_025,
    )
    history = Payout(
        payout_id="004",
        team=team,
        first_import_batch=batch,
        first_source_kind="HISTORY",
        created_at=datetime(2026, 11, 11, 12, tzinfo=UTC),
        created_date_ist=date(2026, 11, 11),
        covers_from=date(2026, 11, 7),
        covers_to=date(2026, 11, 9),
        txn_count=1,
        payout_amount_cents=900,
        historical_decision="REJECTED",
        historical_decided_by="Fictional Reviewer",
        historical_decided_at=datetime(2026, 11, 11, 13, tzinfo=UTC),
        historical_reason_code="FRAUD_CARD_RING",
        historical_reason="Fabricated case note.",
    )
    model = ModelVersion(
        scope_key="shared",
        training_fingerprint="fixture-training",
        algorithm="Isolation Forest",
        feature_schema_version="1",
        config={"random_state": 42},
        fit_cutoff_at=datetime(2026, 11, 9, tzinfo=UTC),
        validation_cutoff_at=CUTOFF,
        training_observation_count=30,
        validation_observation_count=15,
        anomaly_threshold=0.6,
        metrics={"n": 15},
        status="ACTIVE",
    )
    run = EvaluationRun(
        batch=batch,
        requester=user,
        scope_key="shared",
        rule_version="1",
        cutoff_at=CUTOFF,
        snapshot_at=CUTOFF,
        input_digest="fixture-run",
        completed_at=CUTOFF,
        status="COMPLETED",
        history_manifest={"pending": ["005"]},
        model_version=model,
        summary={"historical_monitoring": {"window_days": 20}},
    )
    uploaded = UploadedFile(
        batch=batch,
        file_type="pending_payouts",
        filename="fictional-pending.csv",
        sha256="a" * 64,
        bytes_count=200,
        row_count=1,
        inserted_count=1,
        unchanged_count=0,
        imported_source_ids=[{"type": "payout", "id": "005"}],
        validation_summary={"valid": True},
    )
    cluster = FraudCluster(
        run=run,
        seed_payout=history,
        stable_case_key="fictional-case",
        reason_code="FRAUD_CARD_RING",
        summary="Fabricated historical case.",
        evidence=[{"type": "payout", "id": "004"}],
    )
    original = PayoutDecision(
        run=run,
        payout=pending,
        model_version=model,
        decision="MANUAL_REVIEW",
        reason="Check the unusual payout.",
        evidence=EVIDENCE,
        findings={"statistics": {"sample_count": 10}, "fraud": {"matches": []}},
        ml_status="ACTIVE",
        ml_score=0.72,
        ml_is_anomaly=True,
        rule_version="1",
    )
    session.add_all([payment, pending, uploaded, cluster, original])
    session.flush()
    return Records(user, batch, pending, history, run, cluster, original)


def test_upgrade_has_exact_application_tables_and_source_columns(
    engine: Engine, migrate_schema: Callable[[str], None]
) -> None:
    schema = inspect(engine)
    assert set(schema.get_table_names()) == {*Base.metadata.tables, "alembic_version"}
    sources = {
        "teams": (
            "team_id team_name profession owner_name owner_phone business_address city state "
            "onboarded_on status suspended_on payout_bank_routing payout_bank_last4 "
            "signup_device_id first_import_batch_id ingested_at"
        ),
        "transactions": (
            "txn_id team_id created_at collection_date_ist amount_cents tip_cents "
            "payment_method card_fingerprint card_country status customer_name customer_zip "
            "first_import_batch_id ingested_at"
        ),
        "payouts": (
            "payout_id team_id created_at created_date_ist covers_from covers_to txn_count "
            "payout_amount_cents historical_decision historical_decided_by historical_decided_at "
            "historical_reason_code historical_reason source_status first_import_batch_id "
            "first_source_kind disposition_import_batch_id disposition_recorded_at ingested_at"
        ),
    }
    for table, expected in sources.items():
        assert {column["name"] for column in schema.get_columns(table)} == set(expected.split())
    assert {key["name"] for key in schema.get_indexes("transactions")} == {
        "ix_transactions_team_collection_date",
        "ix_transactions_card_fingerprint",
    }
    assert set(sources) <= set(Base.metadata.tables)
    migrate_schema("check")


def test_source_provenance_and_metadata_round_trip(session: Session, records: Records) -> None:
    run_id, decision_id = records.run.run_id, records.decision.decision_id
    session.expire_all()
    payment = session.get(Transaction, "001")
    assert payment.amount_cents == 3_000_000_000
    assert payment.tip_cents == 25
    assert payment.created_at.utcoffset() is not None
    assert payment.collection_date_ist == date(2026, 11, 10)
    assert payment.customer_zip == "00004"
    assert payment.card_fingerprint is None
    assert payment.team.owner_phone == "0000000000"
    assert payment.team.payout_bank_routing == "000000001"
    assert payment.team.payout_bank_last4 == "0002"
    assert payment.first_import_batch_id == records.batch.batch_id
    assert records.pending.payout_amount_cents == 3_000_000_025
    assert records.history.historical_reason == "Fabricated case note."
    run = session.get(EvaluationRun, run_id)
    assert isinstance(run.run_id, UUID)
    assert run.history_manifest == {"pending": ["005"]}
    assert run.batch.files[0].imported_source_ids == [{"type": "payout", "id": "005"}]
    assert run.model_version.config == {"random_state": 42}
    assert run.model_version.anomaly_threshold == 0.6
    decision = session.get(PayoutDecision, decision_id)
    assert decision.evidence == EVIDENCE
    assert decision.findings["statistics"]["sample_count"] == 10
    assert decision.ml_is_anomaly is True
    assert decision.ml_score == 0.72


def assert_rejected(session: Session, statement: Executable, code: str) -> None:
    with pytest.raises(DBAPIError) as failure, session.begin_nested():
        session.execute(statement)
    assert failure.value.orig.sqlstate == code


def test_ready_batch_and_completed_run_digests_are_unique(
    session: Session, records: Records
) -> None:
    duplicate_batch = insert(DatasetBatch).values(
        scope_key=records.batch.scope_key,
        uploaded_by=records.user.user_id,
        mode="INITIAL",
        cutoff_at=CUTOFF,
        status="READY",
        input_digest=records.batch.input_digest,
        imported_at=CUTOFF,
    )
    assert_rejected(session, duplicate_batch, "23505")
    session.execute(duplicate_batch.values(status="FAILED"))
    session.execute(duplicate_batch.values(scope_key="another-scope"))

    duplicate_run = insert(EvaluationRun).values(
        batch_id=records.batch.batch_id,
        requested_by=records.user.user_id,
        scope_key="shared",
        cutoff_at=CUTOFF,
        rule_version="1",
        status="COMPLETED",
        input_digest=records.run.input_digest,
        snapshot_at=CUTOFF,
        completed_at=CUTOFF,
    )
    assert_rejected(session, duplicate_run, "23505")
    session.execute(duplicate_run.values(status="FAILED"))
    session.execute(duplicate_run.values(input_digest="revised-run"))


def test_uploaded_file_type_and_membership_constraints(session: Session, records: Records) -> None:
    file = insert(UploadedFile).values(
        batch_id=records.batch.batch_id,
        file_type="pending_payouts",
        filename="synthetic.csv",
        sha256="b" * 64,
        bytes_count=100,
        row_count=0,
        inserted_count=0,
        unchanged_count=0,
    )
    assert_rejected(session, file, "23505")
    assert_rejected(
        session, file.values(file_type="teams", filename="subdir/synthetic.csv"), "23514"
    )
    assert_rejected(session, file.values(file_type="teams", imported_source_ids={}), "23514")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"team_id": "foreign-unknown", "txn_id": "002"}, "23503"),
        ({"collection_date_ist": date(2026, 11, 9), "txn_id": "002"}, "23514"),
        ({"status": "invalid", "txn_id": "002"}, "23514"),
        ({}, "23505"),
    ],
)
def test_transaction_constraints(
    session: Session, records: Records, changes: dict, code: str
) -> None:
    values = {
        "txn_id": "001",
        "team_id": "0001",
        "created_at": CUTOFF,
        "collection_date_ist": date(2026, 11, 13),
        "first_import_batch_id": records.batch.batch_id,
        "amount_cents": 25,
        "tip_cents": 0,
        "payment_method": "ach",
        "status": "captured",
        "customer_name": "Fictional Customer",
        "customer_zip": "00005",
    }
    values.update(changes)
    assert_rejected(session, insert(Transaction).values(**values), code)


@pytest.mark.parametrize(
    ("decision", "has_cluster", "payout_id", "code"),
    [
        ("APPROVE", False, "005", "23505"),
        ("INVALID", False, "004", "23514"),
        ("REJECT", False, "004", "23514"),
        ("APPROVE", True, "004", "23514"),
        ("APPROVE", False, "missing", "23503"),
    ],
)
def test_automated_decision_constraints(
    session: Session,
    records: Records,
    decision: str,
    has_cluster: bool,
    payout_id: str,
    code: str,
) -> None:
    statement = insert(PayoutDecision).values(
        run_id=records.run.run_id,
        payout_id=payout_id,
        decision=decision,
        cluster_id=records.cluster.cluster_id if has_cluster else None,
        reason="Fabricated decision.",
        ml_status="INSUFFICIENT_HISTORY",
        rule_version="1",
    )
    assert_rejected(session, statement, code)


def override(records: Records, **changes):
    values = {
        "decision_id": records.decision.decision_id,
        "scope_key": "shared",
        "version": 2,
        "previous_decision": "MANUAL_REVIEW",
        "new_decision": "APPROVE",
        "change_reason": "Verified the fictional evidence.",
        "changed_by": records.user.user_id,
    }
    values.update(changes)
    return insert(PayoutDecisionHistory).values(**values)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"change_reason": " \t\n"}, "23514"),
        ({"new_decision": "REJECT"}, "23514"),
        ({"new_decision": "INVALID"}, "23514"),
        ({"version": 1}, "23514"),
        ({"version": 3}, "23514"),
        ({"previous_decision": "APPROVE"}, "23514"),
        ({"changed_by": UUID(int=1)}, "23503"),
    ],
)
def test_history_constraints(session: Session, records: Records, changes: dict, code: str) -> None:
    assert_rejected(session, override(records, **changes), code)


def test_original_and_history_are_append_only(session: Session, records: Records) -> None:
    session.execute(override(records))
    assert_rejected(session, override(records), "23505")
    assert_rejected(session, override(records, cluster_id=records.cluster.cluster_id), "23514")
    session.execute(
        override(
            records,
            version=3,
            previous_decision="APPROVE",
            new_decision="REJECT",
            cluster_id=records.cluster.cluster_id,
            evidence=EVIDENCE,
        )
    )
    session.execute(override(records, scope_key="reviewer-b"))
    for model, values in [
        (PayoutDecision, {"reason": "Changed"}),
        (PayoutDecisionHistory, {"change_reason": "Changed"}),
        (FraudCluster, {"summary": "Changed"}),
    ]:
        assert_rejected(session, update(model).values(**values), "55000")
        assert_rejected(session, delete(model), "55000")
    assert_rejected(session, text("TRUNCATE TABLE payout_decision_history"), "55000")

    events = session.scalars(
        select(PayoutDecisionHistory)
        .where(PayoutDecisionHistory.scope_key == "shared")
        .order_by(PayoutDecisionHistory.version)
    ).all()
    assert [event.version for event in events] == [2, 3]
    assert events[-1].actor.user_id == records.user.user_id
    assert events[-1].new_decision == "REJECT"
    assert records.decision.decision == "MANUAL_REVIEW"


def test_clusters_cannot_cross_runs(session: Session, records: Records) -> None:
    other = EvaluationRun(
        batch=records.batch,
        requester=records.user,
        scope_key="shared",
        cutoff_at=CUTOFF,
        rule_version="2",
    )
    session.add(other)
    session.flush()
    assert_rejected(
        session,
        insert(PayoutDecision).values(
            run_id=other.run_id,
            payout_id=records.pending.payout_id,
            decision="REJECT",
            cluster_id=records.cluster.cluster_id,
            reason="Fabricated.",
            rule_version="2",
            ml_status="INSUFFICIENT_HISTORY",
        ),
        "23503",
    )
    outsider = FraudCluster(
        run=other,
        seed_payout=records.history,
        stable_case_key="fictional-case",
        reason_code="FRAUD_CARD_RING",
        summary="Fabricated.",
    )
    session.add(outsider)
    session.flush()
    assert_rejected(
        session,
        override(
            records,
            new_decision="REJECT",
            cluster_id=outsider.cluster_id,
        ),
        "23503",
    )


def test_pending_disposition_can_be_recorded_once(session: Session, records: Records) -> None:
    session.execute(override(records))
    payout = update(Payout).where(Payout.payout_id == records.pending.payout_id)
    follow_up = DatasetBatch(
        uploader=records.user,
        scope_key="shared",
        mode="INCREMENTAL",
        cutoff_at=CUTOFF,
    )
    session.add(follow_up)
    session.flush()
    assert_rejected(session, payout.values(payout_amount_cents=1), "55000")
    assert_rejected(session, payout.values(historical_decision="MANUAL_APPROVED"), "23514")
    session.execute(
        payout.values(
            historical_decision="MANUAL_APPROVED",
            historical_decided_at=CUTOFF,
            historical_decided_by="Fictional Reviewer",
            disposition_import_batch_id=follow_up.batch_id,
            disposition_recorded_at=CUTOFF,
        )
    )
    session.refresh(records.pending)
    assert records.pending.source_status == "PENDING_DECISION"
    assert records.pending.first_source_kind == "PENDING"
    assert records.pending.first_import_batch_id == records.batch.batch_id
    assert records.pending.disposition_import_batch_id == follow_up.batch_id
    assert records.pending.payout_amount_cents == 3_000_000_025
    assert records.decision.decision == "MANUAL_REVIEW"
    assert session.scalar(select(func.count()).select_from(PayoutDecisionHistory)) == 1
    assert_rejected(session, payout.values(historical_decision="REJECTED"), "55000")


def test_database_session_rolls_back_failed_work(
    test_database_url: SecretStr, engine: Engine
) -> None:
    database = Database(Settings(database_url=test_database_url, migration_database_url=None))
    try:
        with pytest.raises(RuntimeError, match="Synthetic failure"), database.session() as session:
            session.add(
                User(
                    username=str(uuid4()),
                    password_hash="fixture-only",
                    display_name="Demo",
                    role="ANALYST",
                )
            )
            session.flush()
            raise RuntimeError("Synthetic failure")
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(User)) == 0
    finally:
        database.close()


def test_request_sessions_release_and_reuse_the_pooled_connection(
    engine: Engine, test_database_url: SecretStr
) -> None:
    app = create_app(
        Settings(
            database_url=test_database_url,
            migration_database_url=None,
            database_pool_size=1,
            database_max_overflow=0,
            database_timeout_seconds=1,
        )
    )
    sessions: list[Session] = []
    backend_pids: list[int | None] = []

    @app.post("/test/session")
    def write_without_commit(
        session: Annotated[Session, Depends(get_session)], fail: bool = False
    ) -> dict[str, bool]:
        sessions.append(session)
        backend_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
        session.add(
            User(
                username=f"fictional-request-{len(sessions)}",
                password_hash="fixture-only",
                display_name="Demo",
                role="ANALYST",
            )
        )
        session.flush()
        if fail:
            raise HTTPException(status_code=409)
        return {"ok": True}

    with TestClient(app) as client:
        pool = app.state.database.engine.pool
        assert isinstance(pool, QueuePool)
        for fail, expected_status in [(False, 200), (True, 409)]:
            response = client.post("/test/session", params={"fail": fail})
            assert response.status_code == expected_status
            assert pool.checkedout() == 0

    assert sessions[0] is not sessions[1]
    assert backend_pids[0] is not None and backend_pids[0] == backend_pids[1]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_downgrade_upgrade_and_database_health(
    engine: Engine, test_database_url: SecretStr, migrate_schema: Callable[[str], None]
) -> None:
    app = create_app(Settings(database_url=test_database_url, migration_database_url=None))
    with TestClient(app) as client:
        try:
            migrate_schema("downgrade")
            assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["database"] == {"connectivity": "connected", "ready": False}
        finally:
            migrate_schema("upgrade")
        response = client.get("/health")
        assert response.json() == {
            "status": "ok",
            "database": {"connectivity": "connected", "ready": True},
            "evaluation_data": "not_ready",
        }
