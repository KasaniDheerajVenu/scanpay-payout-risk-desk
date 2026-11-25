from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.review_scope import ActorContext
from app.core.security import hash_password
from app.database.session import Database
from app.main import create_app
from app.models import (
    DatasetBatch,
    EvaluationRun,
    Payout,
    PayoutDecision,
    PayoutDecisionHistory,
    Team,
    Transaction,
    UploadedFile,
    User,
)
from app.models.values import FileType, UserRole
from app.schemas.ingestion import ImportFailure, InputFile
from app.services.csv_source import import_options
from app.services.ingestion_service import ingest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PASSWORD = "fictional Phase 4 suite password"
ORIGIN = "http://127.0.0.1:5173"
DEMO_ORIGIN = "https://reviewer.example.test"
INITIAL = import_options("INITIAL", "2025-02-20 10:00")
INCREMENTAL = import_options("INCREMENTAL", "2025-02-24 10:00")


def fixture_data(collection: str) -> dict[FileType, bytes]:
    directory = FIXTURES / collection
    return {
        kind: path.read_bytes()
        for kind in FileType
        if (path := directory / f"{kind}.csv").is_file()
    }


def source_files(data: dict[FileType, bytes], prefix: str = "") -> dict[FileType, InputFile]:
    return {kind: InputFile(f"{prefix}{kind}.csv", BytesIO(data)) for kind, data in data.items()}


@pytest.fixture
def context(migrate_schema, test_database_url):
    migrate_schema("downgrade")
    migrate_schema("upgrade")
    settings = Settings(
        database_url=test_database_url,
        auth_signing_secret="fabricated phase four integration testing secret",
        app_mode="local",
        trusted_origins=(ORIGIN,),
    )
    database = Database(settings)
    password_hash = hash_password(PASSWORD)
    ids = {}
    try:
        with database.session() as session:
            for role in UserRole:
                user = User(
                    username=f"fixture-{role.lower()}",
                    display_name=f"Synthetic {role}",
                    role=role,
                    password_hash=password_hash,
                )
                session.add(user)
                session.flush()
                ids[role] = user.user_id
            session.commit()
        actor = ActorContext(
            ids[UserRole.ADMIN], "fixture-admin", "Synthetic ADMIN", UserRole.ADMIN, 1, "local"
        )
        yield database, settings, actor
    finally:
        database.close()
        migrate_schema("downgrade")
        migrate_schema("upgrade")


def import_initial(context):
    return ingest(*context, source_files(fixture_data("initial")), INITIAL)[0]


def batch_statuses(database: Database) -> list[str]:
    with database.session() as session:
        return sorted(session.scalars(select(DatasetBatch.status)))


def test_initial_import_and_pending_membership_survive_source_deletion(context, tmp_path):
    database, _, actor = context
    data = fixture_data("initial")
    paths = {kind: tmp_path / f"original-{kind}.csv" for kind in data}
    for kind, path in paths.items():
        path.write_bytes(data[kind])
    with ExitStack() as stack:
        files = {
            kind: InputFile(path.name, stack.enter_context(path.open("rb")))
            for kind, path in paths.items()
        }
        batch, created = ingest(*context, files, INITIAL)
    for path in paths.values():
        path.unlink()
    assert created and batch.status == "READY" and batch.uploaded_by == actor.user_id
    assert batch.imported_at > batch.cutoff_at
    with database.session() as session:
        team = session.get(Team, "0001")
        assert team.owner_phone == "0005550001" and team.payout_bank_last4 == "0001"
        assert session.get(Team, "0002").status == "suspended"
        txn = session.get(Transaction, "000305")
        assert txn.card_fingerprint is None and txn.customer_zip == "00420"
        assert txn.created_at.utcoffset() is not None
        assert session.get(Transaction, "000302").status == "declined"
        assert session.get(Transaction, "000304").status == "refunded"
        historical = session.get(Payout, "000202")
        assert historical.historical_reason_code == "FRAUD_CARD_RING"
        assert historical.historical_reason == "Fabricated fixture case."
        assert session.get(Payout, "000401").payout_amount_cents == 3125
        stored_files = {
            file.file_type: file for file in session.get(DatasetBatch, batch.batch_id).files
        }
        pending = stored_files["pending_payouts"]
        assert pending.imported_source_ids == [{"payout_id": "000401"}, {"payout_id": "000402"}]
        assert (
            stored_files["history_payouts"].validation_summary["incomplete_historical_windows"] >= 1
        )
        assert (
            "000201"
            in stored_files["history_payouts"].validation_summary[
                "incomplete_historical_payout_ids"
            ]
        )


def test_incremental_requires_history_then_reuses_existing_transaction_and_provenance(context):
    data = fixture_data("incremental")
    data.pop(FileType.HISTORY_PAYOUTS)
    with pytest.raises(ImportFailure, match="initial_import_required"):
        ingest(*context, source_files(data), INCREMENTAL)
    initial = import_initial(context)
    first, created = ingest(*context, source_files(data), INCREMENTAL)
    assert created
    repeated, created = ingest(*context, source_files(data, "renamed-"), INCREMENTAL)
    assert not created and repeated.batch_id == first.batch_id
    latest = FileType.LATEST_TRANSACTIONS
    data[latest] = data[latest].splitlines(keepends=True)[0]
    next_batch, created = ingest(*context, source_files(data), INCREMENTAL)
    assert created
    files = {file.file_type: file for file in next_batch.files}
    assert files["pending_payouts"].unchanged_count == 1
    assert files["pending_payouts"].imported_source_ids == [{"payout_id": "000403"}]
    with context[0].session() as session:
        assert session.get(Payout, "000401").first_import_batch_id == initial.batch_id
        assert session.get(Transaction, "000306").first_import_batch_id == first.batch_id


def test_identical_rows_are_counted_but_do_not_double_allocate_transactions(context):
    data = fixture_data("initial")
    key = FileType.LATEST_TRANSACTIONS
    data[key] += data[key].splitlines(keepends=True)[1]
    batch, _ = ingest(*context, source_files(data), INITIAL)
    file = next(file for file in batch.files if file.file_type == key)
    assert (file.row_count, file.inserted_count, file.unchanged_count) == (6, 5, 1)
    assert len(file.imported_source_ids) == 5


@pytest.mark.parametrize(
    ("file_type", "old", "new", "issue"),
    [
        (FileType.PENDING_PAYOUTS, b",2,31.25,", b",1,31.25,", "payout_count_mismatch"),
        (FileType.PENDING_PAYOUTS, b"31.25", b"31.26", "payout_amount_mismatch"),
        (
            FileType.PENDING_PAYOUTS,
            b"000402,0002,2025-02-19",
            b"000402,0002,2025-02-18",
            "mixed_pending_creation_dates",
        ),
        (FileType.PENDING_PAYOUTS, b",2025-02-15,", b",2025-02-16,", "captured_outside_coverage"),
        (FileType.PENDING_PAYOUTS, b",2025-02-15,", b",2025-02-14,", "invalid_settlement_window"),
        (FileType.PENDING_PAYOUTS, b"17:30:00", b"16:30:00", "invalid_settlement_time"),
        (FileType.LATEST_TRANSACTIONS, b"000301,0001", b"000301,9999", "unknown_team"),
        (
            FileType.HISTORY_PAYOUTS,
            b"2025-02-12 18:01:00",
            b"2025-02-12 17:00:00",
            "invalid_disposition_timestamp",
        ),
    ],
)
def test_validation_rolls_back_every_source_table(context, file_type, old, new, issue):
    data = fixture_data("initial")
    assert old in data[file_type]
    data[file_type] = data[file_type].replace(old, new, 1)
    with pytest.raises(ImportFailure, match=issue):
        ingest(*context, source_files(data), INITIAL)
    with context[0].session() as session:
        for model in (Team, Transaction, Payout, UploadedFile):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        failure = session.scalar(select(DatasetBatch))
        assert failure.status == "FAILED" and failure.error_summary["code"] == issue
        assert failure.imported_at is None


def test_overlapping_payouts_and_conflicting_duplicate_ids_fail(context):
    data = fixture_data("initial")
    pending = FileType.PENDING_PAYOUTS
    data[pending] += data[pending].splitlines(keepends=True)[1].replace(b"000401", b"000499")
    with pytest.raises(ImportFailure, match="overlapping_payout_coverage"):
        ingest(*context, source_files(data), INITIAL)
    data = fixture_data("initial")
    key = FileType.LATEST_TRANSACTIONS
    data[key] += data[key].splitlines(keepends=True)[1].replace(b"10.00", b"10.01")
    with pytest.raises(ImportFailure, match="source_conflict") as exc:
        ingest(*context, source_files(data), INITIAL)
    assert exc.value.status == 409
    assert batch_statuses(context[0]) == ["FAILED", "FAILED"]


def test_disjoint_coverage_cannot_split_one_team_settlement(context):
    data = fixture_data("initial")
    key = FileType.HISTORY_PAYOUTS
    data[key] = data[key].replace(
        b"2025-02-08,2025-02-10,2,20.00", b"2025-02-08,2025-02-08,2,20.00"
    )
    data[key] += (
        b"000203,0001,2025-02-12 17:30:00,2025-02-09,2025-02-10,0,0.00,"
        b"AUTO_APPROVED,System,2025-02-12 18:01:00,,\n"
    )
    with pytest.raises(ImportFailure, match="duplicate_settlement_payout"):
        ingest(*context, source_files(data), INITIAL)
    assert batch_statuses(context[0]) == ["FAILED"]


def test_optional_teams_and_history_and_assessment_counts(context):
    original = fixture_data("initial")
    with pytest.raises(ImportFailure, match="assessment_count_mismatch"):
        ingest(
            *context, source_files(original), import_options("INITIAL", "2025-02-20 10:00", True)
        )
    data = original.copy()
    teams, pending, latest = FileType.TEAMS, FileType.PENDING_PAYOUTS, FileType.LATEST_TRANSACTIONS
    for index in range(3, 25):
        team = f"{index:04}".encode()
        data[teams] += (
            original[teams].splitlines(keepends=True)[1].replace(b"0001,", team + b",", 1)
        )
        if index <= 20:
            payout_id = f"004{index:03}".encode()
            data[pending] += (
                original[pending]
                .splitlines(keepends=True)[2]
                .replace(b"000402,0002,", payout_id + b"," + team + b",", 1)
            )
            txn_id = f"003{index:03}".encode()
            data[latest] += (
                original[latest]
                .splitlines(keepends=True)[-1]
                .replace(b"000305,0002,", txn_id + b"," + team + b",", 1)
            )
    assert ingest(
        *context, source_files(data), import_options("INITIAL", "2025-02-20 10:00", True)
    )[1]
    update = fixture_data("incremental")
    update[teams] = original[teams].splitlines(keepends=True)[0] + original[teams].splitlines(
        keepends=True
    )[1].replace(b"0001,", b"9999,", 1)
    history = FileType.HISTORY_TRANSACTIONS
    update[history] = original[history].splitlines(keepends=True)[0] + original[history].splitlines(
        keepends=True
    )[1].replace(b"000101,0001,", b"999901,9999,", 1)
    assert ingest(*context, source_files(update), INCREMENTAL)[1]
    with context[0].session() as session:
        assert session.scalar(select(func.count()).select_from(Team)) == 25
        assert session.get(Transaction, "999901").team_id == "9999"


def test_disposition_preserves_original_and_immutable_application_decisions(context):
    initial = import_initial(context)
    database, _, actor = context
    with database.session() as session:
        now = datetime.now(UTC)
        run = EvaluationRun(
            batch_id=initial.batch_id,
            scope_key="local",
            requested_by=actor.user_id,
            cutoff_at=initial.cutoff_at,
            snapshot_at=now,
            completed_at=now,
            status="COMPLETED",
            input_digest="verification-digest",
            rule_version="v1",
        )
        decision = PayoutDecision(
            run=run,
            payout_id="000401",
            decision="MANUAL_REVIEW",
            reason="Synthetic reason",
            ml_status="INSUFFICIENT_HISTORY",
            rule_version="v1",
        )
        session.add(
            PayoutDecisionHistory(
                original_decision=decision,
                scope_key="local",
                version=2,
                previous_decision="MANUAL_REVIEW",
                new_decision="APPROVE",
                change_reason="Synthetic override",
                changed_by=actor.user_id,
            )
        )
        session.commit()
    data = fixture_data("incremental")
    history = FileType.HISTORY_PAYOUTS
    changed = data.copy()
    changed[history] = data[history].replace(b"31.25", b"31.26")
    with pytest.raises(ImportFailure, match="source_conflict"):
        ingest(*context, source_files(changed), INCREMENTAL)
    disposition_batch, _ = ingest(*context, source_files(data), INCREMENTAL)
    repeated, created = ingest(
        *context, source_files(data), import_options("INCREMENTAL", "2025-02-25 10:00")
    )
    assert (
        created and next(f for f in repeated.files if f.file_type == history).unchanged_count == 1
    )
    changed[history] = data[history].replace(b"AUTO_APPROVED", b"REJECTED")
    with pytest.raises(ImportFailure, match="source_conflict"):
        ingest(*context, source_files(changed), INCREMENTAL)
    with database.session() as session:
        payout = session.get(Payout, "000401")
        assert payout.first_import_batch_id == initial.batch_id
        assert payout.first_source_kind == "PENDING" and payout.source_status == "PENDING_DECISION"
        assert payout.historical_decision == "AUTO_APPROVED"
        assert payout.disposition_import_batch_id == disposition_batch.batch_id
        assert payout.disposition_recorded_at > payout.historical_decided_at
        assert session.scalar(text("SELECT decision FROM payout_decisions")) == "MANUAL_REVIEW"
        assert session.scalar(text("SELECT new_decision FROM payout_decision_history")) == "APPROVE"


def test_real_database_failure_rolls_back_and_records_failed_metadata(context):
    database = context[0]
    with database.engine.begin() as connection:
        connection.execute(
            text("""
            ALTER TABLE uploaded_files ADD CONSTRAINT synthetic_simulated_failure
            CHECK (file_type <> 'pending_payouts')
        """)
        )
    try:
        with pytest.raises(IntegrityError):
            import_initial(context)
        assert batch_statuses(database) == ["FAILED"]
        with database.session() as session:
            for model in (Team, Transaction, Payout, UploadedFile):
                assert session.scalar(select(func.count()).select_from(model)) == 0
    finally:
        with database.engine.begin() as connection:
            connection.execute(
                text("""
                ALTER TABLE uploaded_files DROP CONSTRAINT synthetic_simulated_failure
            """)
            )


@pytest.mark.parametrize("different", [False, True])
def test_advisory_lock_serializes_concurrent_replays_and_conflicts(context, different):
    barrier = Barrier(2)

    def attempt(change: bool):
        data = fixture_data("initial")
        if change:
            data[FileType.TEAMS] = data[FileType.TEAMS].replace(b"Workshop 1", b"Studio 1")
        barrier.wait()
        try:
            batch, created = ingest(*context, source_files(data), INITIAL)
            return ("created" if created else "reused", batch.batch_id)
        except ImportFailure as exc:
            return (exc.details["code"], None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (False, different)))
    expected = {"created", "source_conflict" if different else "reused"}
    assert {result[0] for result in results} == expected
    assert batch_statuses(context[0]).count("READY") == 1
    if not different:
        assert results[0][1] == results[1][1]


def write_headers(client: TestClient, origin: str = ORIGIN) -> dict[str, str]:
    response = client.get("/api/auth/csrf")
    assert response.status_code == 200
    return {"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]}


def login(client: TestClient, role: UserRole, origin: str = ORIGIN) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        headers=write_headers(client, origin),
        json={"username": f"fixture-{role.lower()}", "password": PASSWORD},
    )
    assert response.status_code == 200
    return write_headers(client, origin)


def multipart(data: dict[FileType, bytes] | None = None):
    contents = fixture_data("initial") if data is None else data
    return [
        (kind, (f"fixture-{kind}.txt", blob, "application/octet-stream"))
        for kind, blob in contents.items()
    ]


def post_upload(client: TestClient, headers: dict, data=None, files=None):
    return client.post(
        "/api/datasets/upload",
        headers=headers,
        data={"mode": "INITIAL", "cutoff": "2025-02-20 10:00"} | (data or {}),
        files=multipart() if files is None else files,
    )


def test_dataset_http_contract_and_no_raw_source_data(context, caplog, monkeypatch):
    from starlette import formparsers

    opened = []
    make_file = formparsers.SpooledTemporaryFile

    def capture_file(*args, **kwargs):
        file = make_file(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", capture_file)
    database, settings, actor = context
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        headers = login(client, UserRole.ADMIN)
        response = post_upload(client, headers)
        assert response.status_code == 201, response.json()
        assert opened and all(file.closed for file in opened)
        result = response.json()
        batch_id = result["batch_id"]
        assert result["status"] == "READY"
        assert result["counts"] == {
            "rows": 13,
            "inserted": 13,
            "unchanged": 0,
            "dispositions_completed": 0,
        }
        assert post_upload(client, headers).status_code == 200
        assert post_upload(client, headers, {"uploaded_by": str(UUID(int=0))}).status_code == 422
        assert post_upload(client, headers, files=multipart() + multipart()[:1]).status_code == 422
        assert (
            post_upload(client, headers, files=[("incorrect", ("safe.txt", b"x"))]).status_code
            == 422
        )
        assert post_upload(client, headers, files=multipart()[:-1]).status_code == 422
        data = fixture_data("initial")
        latest = FileType.LATEST_TRANSACTIONS
        marker = "PRIVATE_SYNTHETIC_TEST_MARKER"
        data[latest] = data[latest].replace(b"10.00", marker.encode(), 1)
        response = post_upload(client, headers, files=multipart(data))
        assert response.status_code == 422
        assert response.json()["error"]["details"] == {
            "code": "invalid_value",
            "file_type": "latest_transactions",
            "row": 2,
            "field": "amount_usd",
        }
        assert marker not in response.text and marker not in caplog.text
        assert all(file.closed for file in opened)
        client.cookies.clear()
        login(client, UserRole.VIEWER)
        listing = client.get("/api/datasets")
        assert listing.status_code == 200
        detail = client.get(f"/api/datasets/{batch_id}")
        assert detail.status_code == 200 and len(detail.json()["files"]) == 5
        for private in (
            "0005550001",
            "Fixture Analyst",
            "Fictional",
            "imported_source_ids",
            marker,
        ):
            assert private not in detail.text + listing.text
        assert client.get(f"/api/datasets/{UUID(int=0)}").status_code == 404
    with database.session() as session:
        batch = session.get(DatasetBatch, UUID(batch_id))
        assert batch.uploaded_by == actor.user_id and batch.scope_key == "local"
        assert all("source_rows" not in f.validation_summary for f in batch.files)


@pytest.mark.parametrize(
    ("role", "reason", "expected"),
    [
        (None, "anonymous", 401),
        (UserRole.VIEWER, "role", 403),
        (UserRole.ANALYST, "role", 403),
        (UserRole.ADMIN, "csrf", 403),
        (UserRole.ADMIN, "origin", 403),
        (UserRole.ADMIN, "demo", 403),
    ],
)
def test_upload_rejections_do_not_consume_body(context, role, reason, expected):
    settings = context[1]
    origin = ORIGIN
    if reason == "demo":
        settings = demo_settings(settings)
        origin = DEMO_ORIGIN
    app = create_app(settings)
    reads = []

    async def app_with_probe(scope, receive, send):
        async def tracked_receive():
            reads.append(True)
            return await receive()

        if scope["type"] == "http" and scope["path"] == "/api/datasets/upload":
            await app(scope, tracked_receive, send)
        else:
            await app(scope, receive, send)

    with TestClient(app_with_probe, base_url=origin) as client:
        headers = login(client, role, origin) if role is not None else {}
        if reason == "csrf":
            headers["X-CSRF-Token"] = "invalid"
        if reason == "origin":
            headers["Origin"] = "https://untrusted.example.test"
        assert post_upload(client, headers).status_code == expected
    assert not reads
    assert batch_statuses(context[0]) == []


def demo_settings(settings: Settings) -> Settings:
    return Settings(
        database_url=settings.database_url,
        auth_signing_secret=settings.auth_signing_secret,
        app_mode="demo",
        trusted_origins=(DEMO_ORIGIN,),
    )


def test_demo_preset_is_allowlisted_and_shared_without_local_visibility(context):
    database, settings, actor = context
    local_batch = import_initial(context)
    with TestClient(create_app(demo_settings(settings)), base_url=DEMO_ORIGIN) as client:
        headers = login(client, UserRole.ADMIN, DEMO_ORIGIN)
        route = "/api/datasets/demo-import"
        assert (
            client.post(route, headers=headers, json={"preset_id": "../invented"}).status_code
            == 422
        )
        assert (
            client.post(
                route, headers=headers, json={"preset_id": "synthetic-v1", "scope_key": "local"}
            ).status_code
            == 422
        )
        result = client.post(route, headers=headers, json={"preset_id": "synthetic-v1"})
        assert result.status_code == 201, result.json()
        assert (
            client.post(route, headers=headers, json={"preset_id": "synthetic-v1"}).status_code
            == 200
        )
        demo_id = result.json()["batch_id"]
        client.cookies.clear()
        headers = login(client, UserRole.VIEWER, DEMO_ORIGIN)
        assert (
            client.post(route, headers=headers, json={"preset_id": "synthetic-v1"}).status_code
            == 403
        )
        assert {batch["batch_id"] for batch in client.get("/api/datasets").json()} == {demo_id}
        assert client.get(f"/api/datasets/{demo_id}").status_code == 200
        assert client.get(f"/api/datasets/{local_batch.batch_id}").status_code == 404
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        headers = login(client, UserRole.ADMIN)
        assert (
            client.post(route, headers=headers, json={"preset_id": "synthetic-v1"}).status_code
            == 403
        )
        assert client.get(f"/api/datasets/{demo_id}").status_code == 404
    with database.session() as session:
        batch = session.get(DatasetBatch, UUID(demo_id))
        assert batch.scope_key == "demo" and batch.uploaded_by == actor.user_id


@pytest.mark.parametrize("limit", ["per_file", "aggregate", "row", "field"])
def test_upload_limits_and_partial_spool_cleanup(context, limit, monkeypatch):
    from starlette import formparsers

    opened = []
    make_file = formparsers.SpooledTemporaryFile

    def capture_file(*args, **kwargs):
        file = make_file(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", capture_file)
    options = {
        "per_file": {"upload_max_file_bytes": 220},
        "aggregate": {"upload_max_total_bytes": 750},
        "row": {"upload_max_rows_per_file": 1},
        "field": {"upload_max_field_chars": 8},
    }[limit]
    settings = context[1].model_copy(update=options)
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        headers = login(client, UserRole.ADMIN)
        if limit == "aggregate":
            request = client.build_request(
                "POST",
                "/api/datasets/upload",
                headers=headers,
                data={"mode": "INITIAL", "cutoff": "2025-02-20 10:00"},
                files=multipart(),
            )
            chunks = list(request.stream)
            request = client.build_request(
                "POST",
                request.url,
                headers={**headers, "Content-Type": request.headers["content-type"]},
                content=iter(chunks),
            )
            assert "content-length" not in request.headers
            response = client.send(request)
        else:
            response = post_upload(client, headers)
        assert response.status_code == 413, response.json()
    assert all(file.closed for file in opened)
    assert batch_statuses(context[0]).count("READY") == 0


def test_local_cli_reuses_ingestion_and_rejects_other_roles(context, monkeypatch, capsys):
    from scripts import import_dataset

    monkeypatch.setattr(import_dataset, "Settings", lambda: context[1])
    monkeypatch.setattr(import_dataset, "getpass", lambda _: PASSWORD)
    args = ["--mode", "INITIAL", "--cutoff", "2025-02-20T10:00:00+05:30"]
    for kind in FileType:
        args.extend(
            [f"--{kind.value.replace('_', '-')}", str(FIXTURES / "initial" / f"{kind}.csv")]
        )
    assert import_dataset.main(args + ["--username", "fixture-analyst"]) == 1
    assert import_dataset.main(args + ["--username", "fixture-admin"]) == 0
    assert batch_statuses(context[0]) == ["READY"]
    captured = capsys.readouterr()
    assert '"status": "READY"' in captured.out and PASSWORD not in captured.out + captured.err


def test_database_outage_returns_sanitized_503_for_login_and_upload():
    with socket(AF_INET, SOCK_STREAM) as unserved:
        unserved.bind(("127.0.0.1", 0))
        port = unserved.getsockname()[1]
        settings = Settings(
            database_url=f"postgresql+psycopg://synthetic@127.0.0.1:{port}/scanpay_test",
            database_timeout_seconds=1,
            auth_signing_secret="fabricated verification signing secret",
            trusted_origins=(ORIGIN,),
        )
        app = create_app(settings)
        with TestClient(app, base_url=ORIGIN) as client:
            login_response = client.post(
                "/api/auth/login",
                headers=write_headers(client),
                json={"username": "fixture-admin", "password": PASSWORD},
            )
            client.cookies.set(
                "scanpay_session",
                app.state.auth.credentials.issue_session(UUID(int=1), auth_version=1),
            )
            upload_response = post_upload(client, write_headers(client))
            for response in (login_response, upload_response, client.get("/api/auth/me")):
                assert response.status_code == 503
                assert response.json() == {
                    "error": {"code": "service_unavailable", "message": "Service Unavailable"}
                }
