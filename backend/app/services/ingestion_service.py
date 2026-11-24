from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import AppMode, Settings
from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models import DatasetBatch, Payout, Team, Transaction, UploadedFile
from app.models.values import BatchMode, BatchStatus, FileType, SourceKind
from app.repositories.datasets import find_ready_import, lock_imports, ready_initial_exists
from app.repositories.sources import find_sources
from app.schemas.ingestion import ImportFailure, ImportOptions, InputFile, ParsedFile, SourceRow
from app.services.csv_source import parse_source
from app.services.reconciliation_service import PayoutWindow, reconcile

REQUIRED_INCREMENTAL_FILES = {FileType.LATEST_TRANSACTIONS, FileType.PENDING_PAYOUTS}
ID_FIELDS = {
    FileType.TEAMS: "team_id",
    FileType.HISTORY_TRANSACTIONS: "txn_id",
    FileType.LATEST_TRANSACTIONS: "txn_id",
    FileType.HISTORY_PAYOUTS: "payout_id",
    FileType.PENDING_PAYOUTS: "payout_id",
}
DISPOSITION_FIELDS = (
    "historical_decision",
    "historical_decided_by",
    "historical_decided_at",
    "historical_reason_code",
    "historical_reason",
)


def dataset_scope(mode: AppMode) -> str:
    return "demo" if mode == "demo" else "local"


@dataclass
class FileProgress:
    source: ParsedFile
    inserted: int = 0
    unchanged: int = 0
    dispositions: int = 0
    ids: set[str] = field(default_factory=set)

    def record(self, source_id: str, *, inserted: bool = False, disposition: bool = False) -> None:
        self.ids.add(source_id)
        self.inserted += int(inserted)
        self.dispositions += int(disposition)
        self.unchanged += int(not (inserted or disposition))

    def metadata(self, incomplete_history: list[str]) -> UploadedFile:
        return UploadedFile(
            file_type=self.source.file_type,
            filename=self.source.filename,
            sha256=self.source.sha256,
            bytes_count=self.source.bytes_count,
            row_count=len(self.source.rows),
            inserted_count=self.inserted,
            unchanged_count=self.unchanged,
            validation_summary={
                "dispositions_completed": self.dispositions,
                "incomplete_historical_windows": len(incomplete_history),
                "incomplete_historical_payout_ids": sorted(incomplete_history),
            },
            imported_source_ids=[
                {ID_FIELDS[self.source.file_type]: key} for key in sorted(self.ids)
            ],
        )


def prepare_files(
    files: dict[FileType, InputFile], options: ImportOptions, settings: Settings
) -> dict[FileType, FileProgress]:
    required = set(FileType) if options.mode == BatchMode.INITIAL else REQUIRED_INCREMENTAL_FILES
    for kind in sorted(required - files.keys()):
        raise ImportFailure("missing_file", file_type=kind)

    remaining = settings.upload_max_total_bytes
    progress = {}
    for kind in FileType:
        if kind not in files:
            continue
        parsed = parse_source(kind, files[kind], options, settings, remaining)
        remaining -= parsed.bytes_count
        if not parsed.rows and (
            options.mode == BatchMode.INITIAL or kind == FileType.PENDING_PAYOUTS
        ):
            raise ImportFailure("empty_file", file_type=kind)
        progress[kind] = FileProgress(parsed)
    if options.assessment:
        for kind, expected in ((FileType.TEAMS, 24), (FileType.PENDING_PAYOUTS, 20)):
            unique = {row.values[ID_FIELDS[kind]] for row in progress[kind].source.rows}
            if len(unique) != expected:
                raise ImportFailure("assessment_count_mismatch", file_type=kind)
    return progress


def input_digest(files: dict[FileType, FileProgress], options: ImportOptions) -> str:
    manifest = {
        "mode": options.mode,
        "cutoff": options.cutoff.isoformat(),
        "assessment": options.assessment,
        "files": {kind: item.source.sha256 for kind, item in files.items()},
    }
    return sha256(dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def conflict(kind: FileType, row: SourceRow) -> ImportFailure:
    return ImportFailure(
        "source_conflict", status=409, file_type=kind, row=row.line, field=ID_FIELDS[kind]
    )


def values_equal(record: Team | Transaction | Payout, values: dict) -> bool:
    return all(getattr(record, field) == value for field, value in values.items())


def source_rows(files: dict[FileType, FileProgress], kind: FileType) -> list[SourceRow]:
    return files[kind].source.rows if kind in files else []


def persist_teams(
    session: Session, batch: DatasetBatch, files: dict[FileType, FileProgress]
) -> dict[str, Team]:
    team_rows = source_rows(files, FileType.TEAMS)
    referenced = {row.values["team_id"] for file in files.values() for row in file.source.rows}
    stored = find_sources(session, Team, Team.team_id, referenced)
    teams = {key: team for key, (team, scope) in stored.items() if scope == batch.scope_key}
    for row in team_rows:
        team_id = row.values["team_id"]
        existing = teams.get(team_id)
        if existing is not None:
            if not values_equal(existing, row.values):
                raise conflict(FileType.TEAMS, row)
            files[FileType.TEAMS].record(team_id)
            continue
        if team_id in stored:
            raise conflict(FileType.TEAMS, row)
        team = Team(**row.values, first_import_batch_id=batch.batch_id)
        teams[team_id] = team
        session.add(team)
        files[FileType.TEAMS].record(team_id, inserted=True)
    session.flush()
    return teams


def check_team(
    teams: dict[str, Team], kind: FileType, row: SourceRow, activity_date_field: str
) -> None:
    team = teams.get(row.values["team_id"])
    if team is None:
        raise ImportFailure("unknown_team", file_type=kind, row=row.line, field="team_id")
    if row.values[activity_date_field] < team.onboarded_on:
        raise ImportFailure(
            "activity_before_onboarding", file_type=kind, row=row.line, field="created_at_ist"
        )


def persist_transactions(
    session: Session,
    batch: DatasetBatch,
    files: dict[FileType, FileProgress],
    teams: dict[str, Team],
) -> None:
    kinds = (FileType.HISTORY_TRANSACTIONS, FileType.LATEST_TRANSACTIONS)
    ids = {row.values["txn_id"] for kind in kinds for row in source_rows(files, kind)}
    stored = find_sources(session, Transaction, Transaction.txn_id, ids)
    for kind in kinds:
        for row in source_rows(files, kind):
            check_team(teams, kind, row, "collection_date_ist")
            txn_id = row.values["txn_id"]
            if txn_id in stored:
                record, scope = stored[txn_id]
                if scope != batch.scope_key or not values_equal(record, row.values):
                    raise conflict(kind, row)
                files[kind].record(txn_id)
                continue
            record = Transaction(**row.values, first_import_batch_id=batch.batch_id)
            session.add(record)
            stored[txn_id] = (record, batch.scope_key)
            files[kind].record(txn_id, inserted=True)
    session.flush()


def persist_payouts(
    session: Session,
    batch: DatasetBatch,
    files: dict[FileType, FileProgress],
    teams: dict[str, Team],
) -> list[PayoutWindow]:
    kinds = (FileType.HISTORY_PAYOUTS, FileType.PENDING_PAYOUTS)
    history_ids, pending_ids = (
        {row.values["payout_id"] for row in source_rows(files, kind)} for kind in kinds
    )
    stored = find_sources(session, Payout, Payout.payout_id, history_ids | pending_ids)
    selected: dict[str, PayoutWindow] = {}
    recorded_at = session.scalar(select(func.clock_timestamp()))
    for kind in kinds:
        historical = kind == FileType.HISTORY_PAYOUTS
        source_kind = SourceKind.HISTORY if historical else SourceKind.PENDING
        for row in source_rows(files, kind):
            check_team(teams, kind, row, "created_date_ist")
            payout_id = row.values["payout_id"]
            if payout_id in history_ids and payout_id in pending_ids:
                raise conflict(kind, row)
            if payout_id not in stored:
                payout = Payout(
                    **row.values,
                    first_import_batch_id=batch.batch_id,
                    first_source_kind=source_kind,
                )
                session.add(payout)
                stored[payout_id] = (payout, batch.scope_key)
                files[kind].record(payout_id, inserted=True)
            else:
                payout, scope = stored[payout_id]
                if scope != batch.scope_key:
                    raise conflict(kind, row)
                if values_equal(payout, row.values):
                    files[kind].record(payout_id)
                elif (
                    historical
                    and payout.first_source_kind == SourceKind.PENDING
                    and payout.historical_decision is None
                    and values_equal(
                        payout,
                        {
                            key: value
                            for key, value in row.values.items()
                            if key not in DISPOSITION_FIELDS
                        },
                    )
                ):
                    for key in DISPOSITION_FIELDS:
                        setattr(payout, key, row.values[key])
                    payout.disposition_import_batch_id = batch.batch_id
                    payout.disposition_recorded_at = recorded_at
                    files[kind].record(payout_id, disposition=True)
                else:
                    raise conflict(kind, row)
            selected.setdefault(payout_id, PayoutWindow(payout, kind, row.line))
    session.flush()
    return list(selected.values())


def record_failed(
    database: Database,
    metadata: dict,
    failure: ImportFailure,
) -> None:
    try:
        with database.session() as session:
            batch = DatasetBatch(
                **metadata, status=BatchStatus.FAILED, error_summary=failure.details
            )
            session.add(batch)
            session.commit()
    except SQLAlchemyError:
        return


def ingest(
    database: Database,
    settings: Settings,
    actor: ActorContext,
    files: dict[FileType, InputFile],
    options: ImportOptions,
) -> tuple[DatasetBatch, bool]:
    metadata = {
        "uploaded_by": actor.user_id,
        "scope_key": dataset_scope(settings.app_mode),
        "mode": options.mode,
        "cutoff_at": options.cutoff,
    }
    try:
        progress = prepare_files(files, options, settings)
        digest = input_digest(progress, options)
        metadata["input_digest"] = digest
        with database.session() as session:
            lock_imports(session)
            existing = find_ready_import(session, metadata["scope_key"], digest)
            if existing is not None:
                return existing, False
            if options.mode == BatchMode.INCREMENTAL and not ready_initial_exists(
                session, metadata["scope_key"]
            ):
                raise ImportFailure("initial_import_required")
            batch = DatasetBatch(**metadata, status=BatchStatus.UPLOADED)
            session.add(batch)
            session.flush()
            batch.status = BatchStatus.VALIDATING
            teams = persist_teams(session, batch, progress)
            persist_transactions(session, batch, progress, teams)
            payouts = persist_payouts(session, batch, progress, teams)
            incomplete = reconcile(session, batch.scope_key, payouts, options.cutoff)
            batch.files = [
                item.metadata(incomplete if kind == FileType.HISTORY_PAYOUTS else [])
                for kind, item in progress.items()
            ]
            batch.status = BatchStatus.READY
            batch.imported_at = session.scalar(select(func.clock_timestamp()))
            session.commit()
            return batch, True
    except ImportFailure as failure:
        record_failed(database, metadata, failure)
        raise
    except OSError:
        failure = ImportFailure("input_unavailable", status=503)
        record_failed(database, metadata, failure)
        raise failure from None
    except SQLAlchemyError:
        record_failed(database, metadata, ImportFailure("import_unavailable"))
        raise
