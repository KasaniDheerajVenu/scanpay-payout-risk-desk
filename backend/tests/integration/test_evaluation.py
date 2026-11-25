from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from app.api.dependencies import CurrentUser
from app.core.config import Settings
from app.core.review_scope import ActorContext
from app.core.security import hash_password
from app.database.session import Database
from app.main import create_app
from app.models import (
    DatasetBatch,
    EvaluationRun,
    ModelVersion,
    PayoutDecision,
    PayoutDecisionHistory,
    Transaction,
    User,
)
from app.models.values import BatchMode, BatchStatus, FileType, TransactionStatus, UserRole
from app.risk_engine.inputs import Eligibility, EvaluationConfig, MonitoringCoverage
from app.schemas.evaluation import EvaluationError, RunFailure
from app.schemas.ingestion import InputFile
from app.services.csv_source import import_options
from app.services.evaluation_input_service import capture_inputs, materialize
from app.services.evaluation_manifest import fingerprint
from app.services.evaluation_service import claim_run, fail_run, prepare_run
from app.services.ingestion_service import ingest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SAMPLE = Path(__file__).resolve().parents[3] / "sample_data" / "synthetic-v1"
ORIGIN = "http://127.0.0.1:5173"
PASSWORD = "fabricated evaluation test password"
INITIAL_CUTOFF = "2025-02-20 10:00"
INCREMENTAL_CUTOFF = "2025-02-24 10:00"


@pytest.fixture
def evaluation_db(migrate_schema, test_database_url):
    migrate_schema("downgrade")
    migrate_schema("upgrade")
    settings = Settings(
        database_url=test_database_url,
        app_mode="local",
        trusted_origins=(ORIGIN,),
        auth_signing_secret="fabricated evaluation integration signing secret",
    )
    database = Database(settings)
    actors = {}
    try:
        with database.session() as session:
            for role in (UserRole.ADMIN, UserRole.ANALYST):
                user = User(
                    username=f"evaluation-{role.lower()}",
                    display_name="Synthetic Reviewer",
                    role=role,
                    password_hash=hash_password(PASSWORD),
                )
                session.add(user)
                session.flush()
                actors[role] = ActorContext(
                    user.user_id, user.username, user.display_name, role, user.auth_version, "local"
                )
            session.commit()
        yield database, settings, actors[UserRole.ADMIN], actors[UserRole.ANALYST]
    finally:
        database.close()
        migrate_schema("downgrade")
        migrate_schema("upgrade")


def input_files(directory: Path) -> dict[FileType, bytes]:
    return {
        kind: path.read_bytes()
        for kind in FileType
        if (path := directory / f"{kind}.csv").is_file()
    }


def add_batch(context, files, *, initial=False, cutoff=None):
    database, settings, admin, _ = context
    mode = BatchMode.INITIAL if initial else BatchMode.INCREMENTAL
    cutoff = cutoff or (INITIAL_CUTOFF if initial else INCREMENTAL_CUTOFF)
    blobs = {kind: InputFile(f"{kind}.csv", BytesIO(data)) for kind, data in files.items()}
    return ingest(database, settings, admin, blobs, import_options(mode, cutoff))[0]


def initial_batch(context):
    return add_batch(context, input_files(FIXTURES / "initial"), initial=True)


def incremental_files():
    files = input_files(FIXTURES / "incremental")
    files.pop(FileType.HISTORY_PAYOUTS)
    return files


def test_database_only_inputs_preserve_dispositions_and_historical_monitoring(
    evaluation_db, tmp_path
):
    database, settings, admin, _ = evaluation_db
    paths = {kind: tmp_path / f"disposable-{kind}.csv" for kind in FileType}
    for kind, path in paths.items():
        payload = (SAMPLE / f"{kind}.csv").read_bytes()
        if kind == FileType.HISTORY_PAYOUTS:
            approved = b",347.77,AUTO_APPROVED,System,2025-01-29 18:00:00,,"
            assert approved in payload
            payload = payload.replace(
                approved,
                b",347.77,REJECTED,Fixture reviewer,2025-01-29 18:00:00,FRAUD_CARD_RING,"
                b"Fabricated case tied to DEMO-H-09-08-0-1.",
                1,
            )
        path.write_bytes(payload)
    with ExitStack() as stack:
        files = {
            kind: InputFile(path.name, stack.enter_context(path.open("rb")))
            for kind, path in paths.items()
        }
        batch, _ = ingest(
            database, settings, admin, files, import_options("INITIAL", INITIAL_CUTOFF)
        )
    for path in paths.values():
        path.unlink()

    inputs = capture_inputs(database, admin, batch.batch_id)
    assert len(inputs.pending) == len(inputs.batch.pending_ids) == 12
    assert {row.payout.payout_id for row in inputs.pending} == set(inputs.batch.pending_ids)
    assert inputs.snapshot_at >= inputs.batch.imported_at > inputs.batch.cutoff
    assert inputs.batch.payout_date == date(2025, 2, 19)
    assert all(item.payout.coverage_days == 3 for item in inputs.pending)
    assert TransactionStatus.REFUNDED in {a.status for row in inputs.pending for a in row.attempts}
    assert inputs.normal_candidates and inputs.fraud_candidates
    assert inputs.fraud_candidates[0].disposition.reason
    assert {row.eligibility for row in inputs.fraud_candidates} == {Eligibility.FRAUD_INDICATOR}
    assert not set(row.payout.payout_id for row in inputs.normal_candidates).intersection(
        inputs.batch.pending_ids
    )
    assert any(
        row.disposition.reason_code == "OUTLIER_VERIFIED" for row in inputs.normal_candidates
    )
    assert any(row.payout.team_id == "DEMO-08" for row in inputs.normal_candidates)
    assert any(team.team_id == "DEMO-08" and team.status == "suspended" for team in inputs.teams)
    assert inputs.monitoring.first_day == date(2025, 1, 30)
    assert inputs.monitoring.last_day == date(2025, 2, 18)
    assert inputs.monitoring.calendar_days == 20
    assert any(
        day.coverage == MonitoringCoverage.NO_PAYOUT_SCHEDULED for day in inputs.monitoring.days
    )
    totals = [total for day in inputs.monitoring.days for total in day.totals]
    assert any(total.decision == "MANUAL_APPROVED" and total.count > 0 for total in totals)
    assert all(total.count == len(total.payout_ids) for total in totals)


@pytest.mark.parametrize(
    ("defect", "code", "status"),
    [
        ("unknown", "dataset_batch_not_found", 404),
        ("failed", "dataset_batch_not_ready", 409),
        ("scope", "dataset_batch_not_found", 404),
        ("no_file", "pending_membership_missing", 422),
        ("empty", "empty_pending_membership", 422),
        ("missing_row", "pending_payout_missing", 422),
        ("duplicate", "pending_membership_invalid", 422),
        ("cutoff", "invalid_pending_membership", 422),
        ("mismatch", "payout_amount_mismatch", 422),
    ],
)
def test_rejects_inconsistent_batch_selection(evaluation_db, defect, code, status):
    database, _, admin, _ = evaluation_db
    batch = initial_batch(evaluation_db)
    batch_id = uuid4() if defect == "unknown" else batch.batch_id
    with database.session() as session:
        stored = session.get(DatasetBatch, batch.batch_id)
        file = next(item for item in stored.files if item.file_type == FileType.PENDING_PAYOUTS)
        if defect == "failed":
            stored.status = BatchStatus.FAILED
        elif defect == "scope":
            stored.scope_key = "demo"
        elif defect == "no_file":
            session.delete(file)
        elif defect == "empty":
            file.imported_source_ids = []
        elif defect == "missing_row":
            file.imported_source_ids = [{"payout_id": "synthetic-missing"}]
        elif defect == "duplicate":
            file.imported_source_ids = [{"payout_id": "000401"}] * 2
        elif defect == "cutoff":
            stored.cutoff_at = datetime(2025, 2, 1, tzinfo=UTC)
        elif defect == "mismatch":
            session.execute(
                update(Transaction).where(Transaction.txn_id == "000301").values(amount_cents=1)
            )
        session.commit()
    with pytest.raises(EvaluationError, match=code) as ex:
        capture_inputs(database, admin, batch_id)
    assert ex.value.status == status


def test_pending_attempts_are_frozen_by_committed_import_provenance(evaluation_db):
    database, _, admin, _ = evaluation_db
    batch = initial_batch(evaluation_db)
    before = capture_inputs(database, admin, batch.batch_id)
    files = incremental_files()
    files[FileType.LATEST_TRANSACTIONS] += (
        b"SYN-LATE,0001,2025-02-17 13:00:00,7.00,0.50,ach,,,captured,Fabricated patron,00420\n"
    )
    second = add_batch(evaluation_db, files)
    assert capture_inputs(database, admin, second.batch_id).batch.pending_ids == ("000403",)
    with database.session() as session:
        # Simulate an ingestion transaction that started earlier while waiting for the lock.
        session.execute(
            update(Transaction)
            .where(Transaction.txn_id == "SYN-LATE")
            .values(ingested_at=batch.imported_at - timedelta(seconds=1))
        )
        session.commit()
    latest = capture_inputs(database, admin, batch.batch_id)
    assert [row.payout.payout_id for row in latest.pending] == ["000401", "000402"]
    assert {row.status for row in latest.pending[0].attempts} == {
        TransactionStatus.CAPTURED,
        TransactionStatus.DECLINED,
        TransactionStatus.REFUNDED,
    }
    assert "SYN-LATE" not in {row.txn_id for row in latest.pending[0].attempts}
    assert fingerprint(latest) == fingerprint(before)
    repeated = add_batch(evaluation_db, incremental_files(), cutoff="2025-02-25 10:00")
    file = next(item for item in repeated.files if item.file_type == FileType.PENDING_PAYOUTS)
    assert file.unchanged_count == 1
    assert (
        capture_inputs(database, admin, repeated.batch_id).pending[0].payout.payout_id == "000403"
    )


def test_concurrent_disposition_does_not_change_repeatable_read_snapshot(evaluation_db):
    database, _, admin, _ = evaluation_db
    batch = initial_batch(evaluation_db)
    subsequent = add_batch(evaluation_db, incremental_files())
    queued = prepare_run(database, admin, subsequent.batch_id)
    files = input_files(FIXTURES / "incremental")
    files[FileType.HISTORY_PAYOUTS] = files[FileType.HISTORY_PAYOUTS].replace(
        b"2025-02-20 11:00:00", b"2025-02-19 18:00:00"
    )
    config = EvaluationConfig()
    with ThreadPoolExecutor(max_workers=1) as pool, database.session() as reader:
        reader.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        reader.execute(text("SET TRANSACTION READ ONLY"))
        marker = reader.scalar(select(func.clock_timestamp()))
        assert reader.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        second = pool.submit(add_batch, evaluation_db, files).result(timeout=15)
        old = materialize(reader, "local", batch.batch_id, marker, config)
    assert len(old.pending) == 2 and not old.pending_exclusions
    fresh = capture_inputs(database, admin, batch.batch_id)
    assert [row.payout.payout_id for row in fresh.pending] == ["000402"]
    exclusion = fresh.pending_exclusions[0]
    assert exclusion.payout_id == "000401" and exclusion.disposition.version == 2
    assert exclusion.disposition.import_batch_id == second.batch_id
    assert exclusion.disposition.decided_at < batch.cutoff_at < exclusion.disposition.available_at
    assert exclusion.disposition.recorded_at <= exclusion.disposition.available_at
    later = capture_inputs(database, admin, second.batch_id)
    assert "000401" in {row.payout.payout_id for row in later.normal_candidates}
    refreshed = capture_inputs(database, admin, subsequent.batch_id)
    assert fingerprint(refreshed) != queued.run.input_digest
    with database.session() as session:
        stored = session.get(EvaluationRun, queued.run.run_id)
        assert "000401" not in stored.history_manifest["monitoring_payout_ids"]
    assert any(
        "000401" in total.payout_ids for day in refreshed.monitoring.days for total in day.totals
    )


def test_disposition_exclusions_cannot_produce_an_empty_run(evaluation_db):
    database, _, admin, _ = evaluation_db
    files = input_files(FIXTURES / "initial")
    files[FileType.PENDING_PAYOUTS] = b"".join(
        files[FileType.PENDING_PAYOUTS].splitlines(keepends=True)[:2]
    )
    batch = add_batch(evaluation_db, files, initial=True)
    history = input_files(FIXTURES / "incremental")
    history[FileType.HISTORY_PAYOUTS] = history[FileType.HISTORY_PAYOUTS].replace(
        b"2025-02-20 11:00:00", b"2025-02-19 18:00:00"
    )
    add_batch(evaluation_db, history)
    with pytest.raises(EvaluationError, match="empty_evaluation_input") as error:
        prepare_run(database, admin, batch.batch_id)
    assert error.value.excluded_count == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(EvaluationRun)) == 0


def test_cutoff_excludes_future_disposition_and_freezes_persisted_monitoring(evaluation_db):
    database, _, admin, _ = evaluation_db
    initial_batch(evaluation_db)
    batch = add_batch(evaluation_db, incremental_files())
    run = prepare_run(database, admin, batch.batch_id)
    files = input_files(FIXTURES / "incremental")
    files[FileType.HISTORY_PAYOUTS] = files[FileType.HISTORY_PAYOUTS].replace(
        b"2025-02-20 11:00:00", b"2025-02-25 11:00:00"
    )
    latest = add_batch(evaluation_db, files, cutoff="2025-02-26 10:00")
    older = capture_inputs(database, admin, batch.batch_id)
    newer = capture_inputs(database, admin, latest.batch_id)
    assert "000401" not in {row.payout.payout_id for row in older.history}
    assert "000401" in {row.payout.payout_id for row in newer.history}
    assert fingerprint(older) == run.run.input_digest
    with database.session() as session:
        summary = session.get(EvaluationRun, run.run.run_id).summary["historical_monitoring"]
        assert "000401" not in str(summary)
        assert summary["calendar_days"] == 20
        assert summary["unknown_days"] > 0
        recorded = next(day for day in summary["days"] if day["date_ist"] == "2025-02-12")
        totals = {item["decision"]: (item["count"], item["cents"]) for item in recorded["totals"]}
        assert totals == {
            "AUTO_APPROVED": (0, 0),
            "MANUAL_APPROVED": (1, 2000),
            "REJECTED": (1, 2800),
        }


def test_fingerprint_updates_for_backfilled_historical_coverage_and_configuration(evaluation_db):
    database, _, admin, _ = evaluation_db
    batch = initial_batch(evaluation_db)
    original = capture_inputs(database, admin, batch.batch_id)
    assert not original.normal_candidates
    assert any(not row.coverage_complete for row in original.history)
    files = incremental_files()
    history_header = input_files(FIXTURES / "initial")[FileType.HISTORY_TRANSACTIONS].splitlines()[
        0
    ]
    files[FileType.HISTORY_TRANSACTIONS] = history_header + (
        b"\nSYN-HISTORY,0001,2025-02-08 12:00:00,3.00,0.00,ach,,,captured,Synthetic patron,00420\n"
    )
    add_batch(evaluation_db, files)
    backfilled = capture_inputs(database, admin, batch.batch_id)
    assert fingerprint(backfilled) != fingerprint(original)
    assert backfilled.normal_candidates[0].disposition.reason_code == "OUTLIER_VERIFIED"
    assert backfilled.normal_candidates[0].coverage_complete
    reordered = replace(
        backfilled,
        snapshot_at=backfilled.snapshot_at + timedelta(hours=2),
        teams=backfilled.teams[::-1],
        pending=tuple(
            replace(row, attempts=row.attempts[::-1]) for row in backfilled.pending[::-1]
        ),
        history=backfilled.history[::-1],
        batch=replace(backfilled.batch, pending_ids=backfilled.batch.pending_ids[::-1]),
        monitoring=replace(backfilled.monitoring, days=backfilled.monitoring.days[::-1]),
    )
    digest = fingerprint(backfilled)
    assert fingerprint(reordered) == digest
    assert fingerprint(replace(backfilled, pending=backfilled.pending[1:])) != digest
    changed_members = replace(backfilled.batch, pending_ids=backfilled.batch.pending_ids[1:])
    assert fingerprint(replace(backfilled, batch=changed_members)) != digest
    assert (
        fingerprint(replace(backfilled, config=EvaluationConfig(feature_version="test-v2")))
        != digest
    )
    modified = replace(
        backfilled.history[0],
        disposition=replace(backfilled.history[0].disposition, reason="Synthetic amendment"),
    )
    assert fingerprint(replace(backfilled, history=(modified, *backfilled.history[1:]))) != digest


def test_concurrent_run_creation_and_claim_preserve_manifest_and_failure(evaluation_db):
    database, _, admin, analyst = evaluation_db
    batch = initial_batch(evaluation_db)
    with pytest.raises(EvaluationError, match="evaluation_forbidden"):
        prepare_run(database, analyst, batch.batch_id)
    barrier = Barrier(2)

    def prepare(_):
        barrier.wait(timeout=10)
        return prepare_run(database, admin, batch.batch_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(prepare, range(2))
    assert first.run == second.run
    assert first.created != second.created
    assert first.run.requested_by == admin.user_id and first.run.status == "QUEUED"
    with database.session() as session:
        run = session.get(EvaluationRun, first.run.run_id)
        assert run.history_manifest["pending_membership_ids"] == ["000401", "000402"]
        assert run.history_manifest["selected_pending"][0]["import_batch_id"] == str(batch.batch_id)
        assert run.history_manifest["source_input_digest"] == batch.input_digest
        assert "0005550001" not in str(run.history_manifest)
        assert "Fabricated fixture case" not in str(run.history_manifest)
        assert "Fictional Customer" not in str(run.history_manifest)
        assert run.model_version_id is None

    def claim(_):
        try:
            return claim_run(database, admin, first.run.run_id)
        except EvaluationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert claims.count("evaluation_run_not_queued") == 1
    running = next(result for result in claims if result != "evaluation_run_not_queued")
    assert running.status == "RUNNING" and running.started_at >= running.created_at
    with pytest.raises(EvaluationError, match="evaluation_run_not_queued"):
        claim_run(database, admin, first.run.run_id)
    failed = fail_run(database, admin, first.run.run_id, RunFailure.PROCESSING_FAILED)
    assert failed.status == "FAILED" and failed.completed_at >= running.started_at
    with pytest.raises(EvaluationError, match="evaluation_run_terminal"):
        fail_run(database, admin, first.run.run_id, RunFailure.INPUT_INVALID)
    retry = prepare_run(database, admin, batch.batch_id)
    assert retry.created and retry.run.run_id != failed.run_id
    cancelled = fail_run(database, admin, retry.run.run_id, RunFailure.INPUT_INVALID)
    assert cancelled.status == "FAILED" and cancelled.started_at is None
    with database.session() as session:
        assert session.get(EvaluationRun, failed.run_id).error_summary == {
            "code": "processing_failed"
        }
        assert session.scalar(select(func.count()).select_from(PayoutDecision)) == 0
        assert session.scalar(select(func.count()).select_from(ModelVersion)) == 0


def test_authenticated_actor_attribution_and_previous_decisions_unchanged(evaluation_db):
    database, settings, admin, analyst = evaluation_db
    batch = initial_batch(evaluation_db)
    app = create_app(settings)

    @app.post("/api/test/evaluation-input")
    def prepare(request: Request, actor: CurrentUser):
        stored = prepare_run(request.app.state.database, actor, batch.batch_id)
        return {"requested_by": stored.run.requested_by}

    with TestClient(app, base_url=ORIGIN) as client:
        csrf = lambda: {  # noqa: E731
            "Origin": ORIGIN,
            "X-CSRF-Token": client.get("/api/auth/csrf").json()["csrf_token"],
        }
        assert (
            client.post(
                "/api/auth/login",
                headers=csrf(),
                json={
                    "username": admin.username,
                    "password": PASSWORD,
                },
            ).status_code
            == 200
        )
        response = client.post(
            "/api/test/evaluation-input",
            headers=csrf(),
            params={"requested_by": str(analyst.user_id), "scope_key": "demo"},
        )
        assert response.status_code == 200
        assert response.json() == {"requested_by": str(admin.user_id)}
        queued = client.get("/api/evaluations", params={"batch_id": str(batch.batch_id)})
        assert queued.status_code == 200
        assert [(run["status"], run["summary"]) for run in queued.json()] == [("QUEUED", None)]
    with database.session() as session:
        now = datetime.now(UTC)
        prior = EvaluationRun(
            batch_id=batch.batch_id,
            scope_key="local",
            requested_by=admin.user_id,
            cutoff_at=batch.cutoff_at,
            snapshot_at=now,
            completed_at=now,
            status="COMPLETED",
            input_digest="synthetic-previous-run",
            rule_version="previous-rules",
        )
        decision = PayoutDecision(
            run=prior,
            payout_id="000401",
            decision="APPROVE",
            reason="Synthetic prior result",
            ml_status="INSUFFICIENT_HISTORY",
            rule_version="previous-rules",
        )
        event = PayoutDecisionHistory(
            original_decision=decision,
            scope_key="local",
            version=2,
            previous_decision="APPROVE",
            new_decision="MANUAL_REVIEW",
            change_reason="Synthetic prior override",
            changed_by=analyst.user_id,
        )
        session.add(event)
        session.commit()
        ids = prior.run_id, decision.decision_id, event.history_id
    with pytest.raises(EvaluationError, match="evaluation_run_terminal"):
        fail_run(database, admin, ids[0], RunFailure.INPUT_INVALID)
    inputs = capture_inputs(database, admin, batch.batch_id)
    assert "000401" in {row.payout.payout_id for row in inputs.pending}
    assert "000401" not in {row.payout.payout_id for row in inputs.normal_candidates}
    with database.session() as session:
        assert session.get(EvaluationRun, ids[0]).status == "COMPLETED"
        assert session.get(PayoutDecision, ids[1]).decision == "APPROVE"
        assert session.get(PayoutDecisionHistory, ids[2]).new_decision == "MANUAL_REVIEW"


def test_demo_admin_uses_shared_data_scope_without_review_scope_input(evaluation_db):
    local, settings, admin, analyst = evaluation_db
    demo_settings = Settings(
        database_url=settings.database_url,
        app_mode="demo",
        auth_signing_secret=settings.auth_signing_secret,
        trusted_origins=("https://fixture.example.test",),
    )
    demo = Database(demo_settings)
    try:
        reviewer = replace(admin, scope_key=f"demo:{admin.user_id}")
        context = demo, demo_settings, reviewer, analyst
        batch = initial_batch(context)
        assert capture_inputs(demo, reviewer, batch.batch_id).batch.scope_key == "demo"
        with pytest.raises(EvaluationError, match="dataset_batch_not_found"):
            capture_inputs(local, admin, batch.batch_id)
        with pytest.raises(EvaluationError, match="evaluation_forbidden"):
            capture_inputs(demo, analyst, batch.batch_id)
    finally:
        demo.close()


def test_unavailable_evaluation_database_is_sanitized():
    actor = ActorContext(uuid4(), "offline", "Synthetic", UserRole.ADMIN, 1, "local")
    with socket(AF_INET, SOCK_STREAM) as unserved:
        unserved.bind(("127.0.0.1", 0))
        port = unserved.getsockname()[1]
        database = Database(
            Settings(
                database_url=f"postgresql+psycopg://synthetic@127.0.0.1:{port}/scanpay_test",
                database_timeout_seconds=1,
            )
        )
        try:
            with pytest.raises(EvaluationError, match="evaluation_storage_unavailable") as error:
                prepare_run(database, actor, uuid4())
            assert error.value.status == 503 and str(port) not in str(error.value)
        finally:
            database.close()
