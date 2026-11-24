from collections import defaultdict
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.core.permissions import Permission, effective_permissions
from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models import DatasetBatch
from app.models.values import (
    BatchMode,
    BatchStatus,
    FileType,
    HistoricalDecision,
    PaymentMethod,
    Profession,
    ReasonCode,
    SourceKind,
    TeamStatus,
    TransactionStatus,
)
from app.repositories import evaluation_inputs as repository
from app.repositories.datasets import get_batch
from app.repositories.sources import TeamDay
from app.risk_engine.eligibility import historical_eligibility, is_fraud_seed, monitoring_input
from app.risk_engine.inputs import (
    Attempt,
    Disposition,
    EvaluationConfig,
    EvaluationInputs,
    HistoricalObservation,
    PayoutInput,
    PendingExclusion,
    PendingObservation,
    Provenance,
    SelectedBatch,
    TeamInput,
)
from app.schemas.evaluation import EvaluationError
from app.services.ingestion_service import dataset_scope
from app.services.reconciliation_service import (
    payable_mismatch,
    settlement_window,
    within_collection_bounds,
)


def evaluation_scope(database: Database, actor: ActorContext) -> str:
    mode = database.app_mode
    if Permission.RUN_EVALUATIONS not in effective_permissions(actor.role, mode):
        raise EvaluationError("evaluation_forbidden", 403)
    return dataset_scope(mode)


def pending_ids(batch: DatasetBatch) -> tuple[str, ...]:
    file = next((item for item in batch.files if item.file_type == FileType.PENDING_PAYOUTS), None)
    if file is None:
        raise EvaluationError("pending_membership_missing")
    ids = []
    for item in file.imported_source_ids:
        if (
            not isinstance(item, dict)
            or set(item) != {"payout_id"}
            or not isinstance(item["payout_id"], str)
            or not item["payout_id"]
        ):
            raise EvaluationError("pending_membership_invalid")
        ids.append(item["payout_id"])
    if not ids:
        raise EvaluationError("empty_pending_membership")
    if len(set(ids)) != len(ids) or len(ids) > file.row_count:
        raise EvaluationError("pending_membership_invalid")
    return tuple(sorted(ids))


def known_disposition(
    source: repository.PayoutSource, cutoff: datetime, snapshot_at: datetime
) -> Disposition | None:
    payout = source.payout
    if payout.historical_decision is None:
        return None
    decided = payout.historical_decided_at
    if decided is None or decided < payout.created_at:
        raise EvaluationError("inconsistent_disposition")
    if decided > cutoff:
        return None
    initial = payout.first_source_kind == SourceKind.HISTORY
    imported_at = source.disposition_available_at
    imported_by = payout.first_import_batch_id if initial else payout.disposition_import_batch_id
    recorded_at = payout.ingested_at if initial else payout.disposition_recorded_at
    if imported_at is None or imported_by is None or recorded_at is None:
        raise EvaluationError("inconsistent_disposition_provenance")
    if imported_at > snapshot_at:
        return None
    return Disposition(
        HistoricalDecision(payout.historical_decision),
        decided,
        payout.historical_decided_by,
        ReasonCode(payout.historical_reason_code) if payout.historical_reason_code else None,
        payout.historical_reason,
        imported_by,
        imported_at,
        recorded_at,
        1 if initial else 2,
    )


def payout_input(source: repository.PayoutSource) -> PayoutInput:
    payout = source.payout
    return PayoutInput(
        payout.payout_id,
        payout.team_id,
        payout.created_at,
        payout.created_date_ist,
        payout.covers_from,
        payout.covers_to,
        payout.txn_count,
        payout.payout_amount_cents,
        SourceKind(payout.first_source_kind),
        Provenance(payout.first_import_batch_id, source.imported_at, payout.ingested_at),
    )


def team_inputs(session: Session, scope: str, ids: set[str]) -> dict[str, TeamInput]:
    records = {
        team.team_id: TeamInput(
            team.team_id,
            team.team_name,
            Profession(team.profession),
            team.owner_name,
            team.owner_phone,
            team.business_address,
            team.city,
            team.state,
            team.onboarded_on,
            TeamStatus(team.status),
            team.suspended_on,
            team.payout_bank_routing,
            team.payout_bank_last4,
            team.signup_device_id,
            Provenance(team.first_import_batch_id, accepted_at, team.ingested_at),
        )
        for team, accepted_at in repository.read_teams(session, scope, ids)
    }
    if records.keys() != ids:
        raise EvaluationError("referenced_team_missing")
    return records


def read_covered_attempts(
    session: Session, batch: SelectedBatch, payouts: list[PayoutInput], *, pending: bool
) -> dict[str, tuple[Attempt, ...]]:
    windows: dict[str, tuple[date, ...]] = {}
    allocated: set[TeamDay] = set()
    for payout in payouts:
        days = settlement_window(payout.created_at)
        if not (
            days
            and payout.covers_from in days
            and payout.covers_to in days
            and payout.covers_from <= payout.covers_to
        ):
            raise EvaluationError("invalid_payout_window")
        if pending:
            covered = {
                (payout.team_id, day)
                for day in days
                if payout.covers_from <= day <= payout.covers_to
            }
            if allocated & covered:
                raise EvaluationError("overlapping_payout_coverage")
            allocated.update(covered)
        windows[payout.payout_id] = days
    team_days = {(payout.team_id, day) for payout in payouts for day in windows[payout.payout_id]}
    grouped: dict[TeamDay, list[Attempt]] = defaultdict(list)
    for item, accepted_at in repository.read_attempts(
        session, batch.scope_key, team_days, batch.cutoff, batch.imported_at if pending else None
    ):
        grouped[item.team_id, item.collection_date_ist].append(
            Attempt(
                item.txn_id,
                item.team_id,
                item.created_at,
                item.collection_date_ist,
                item.amount_cents,
                item.tip_cents,
                PaymentMethod(item.payment_method),
                item.card_fingerprint,
                item.card_country,
                TransactionStatus(item.status),
                item.customer_name,
                item.customer_zip,
                Provenance(item.first_import_batch_id, accepted_at, item.ingested_at),
            )
        )
    results = {}
    for payout in payouts:
        covered = []
        for day in windows[payout.payout_id]:
            attempts = grouped.get((payout.team_id, day), [])
            if payout.covers_from <= day <= payout.covers_to:
                covered.extend(attempts)
            elif pending and any(item.status == TransactionStatus.CAPTURED for item in attempts):
                raise EvaluationError("captured_outside_coverage")
        results[payout.payout_id] = tuple(
            sorted(covered, key=lambda row: (row.created_at, row.txn_id))
        )
    return results


def reconciliation_issue(payout: PayoutInput, attempts: tuple[Attempt, ...]) -> str | None:
    captures = [attempt for attempt in attempts if attempt.status == TransactionStatus.CAPTURED]
    result = payable_mismatch(
        payout.txn_count,
        payout.payout_amount_cents,
        len(captures),
        sum(item.amount_cents + item.tip_cents for item in captures),
    )
    return result[0] if result is not None else None


def select_pending(
    session: Session, scope: str, batch_id: UUID, snapshot_at: datetime
) -> tuple[SelectedBatch, list[PayoutInput], tuple[PendingExclusion, ...]]:
    batch = get_batch(session, scope, batch_id, FileType.PENDING_PAYOUTS)
    if batch is None:
        raise EvaluationError("dataset_batch_not_found", 404)
    if batch.status != BatchStatus.READY:
        raise EvaluationError("dataset_batch_not_ready", 409)
    if batch.cutoff_at.utcoffset() is None or batch.imported_at is None or not batch.input_digest:
        raise EvaluationError("invalid_batch_metadata")
    ids = pending_ids(batch)
    rows = {
        row.payout.payout_id: row
        for row in repository.read_pending_payouts(session, scope, set(ids))
    }
    if rows.keys() != set(ids):
        raise EvaluationError("pending_payout_missing")
    payouts = []
    excluded = []
    creation_dates = set()
    for key in ids:
        row = rows[key]
        payout = payout_input(row)
        if (
            payout.source_kind != SourceKind.PENDING
            or row.payout.source_status != "PENDING_DECISION"
            or payout.created_at > batch.cutoff_at
            or row.imported_at > batch.imported_at
        ):
            raise EvaluationError("invalid_pending_membership")
        creation_dates.add(payout.created_date)
        disposition = known_disposition(row, batch.cutoff_at, snapshot_at)
        if disposition is None:
            payouts.append(payout)
        else:
            excluded.append(PendingExclusion(key, disposition))
    if len(creation_dates) != 1:
        raise EvaluationError("mixed_pending_creation_dates")
    if not payouts:
        raise EvaluationError("empty_evaluation_input", excluded_count=len(excluded))
    return (
        SelectedBatch(
            batch.batch_id,
            scope,
            BatchMode(batch.mode),
            batch.cutoff_at,
            batch.imported_at,
            batch.input_digest,
            creation_dates.pop(),
            ids,
        ),
        payouts,
        tuple(excluded),
    )


def materialize(
    session: Session, scope: str, batch_id: UUID, snapshot_at: datetime, config: EvaluationConfig
) -> EvaluationInputs:
    batch, pending_payouts, exclusions = select_pending(session, scope, batch_id, snapshot_at)
    windows = [settlement_window(item.created_at) for item in pending_payouts]
    if any(not window for window in windows):
        raise EvaluationError("invalid_payout_window")
    action_start = min(day for window in windows for day in window)
    if batch.payout_date.toordinal() <= 20:
        raise EvaluationError("invalid_monitoring_window")
    past = []
    for source in repository.read_historical_payouts(
        session, scope, batch.payout_date, batch.cutoff
    ):
        disposition = known_disposition(source, batch.cutoff, snapshot_at)
        if disposition is not None:
            payout = payout_input(source)
            if payout.covers_to >= action_start:
                raise EvaluationError("historical_window_overlaps_action")
            past.append((payout, disposition))

    teams = team_inputs(
        session, scope, {row.team_id for row in pending_payouts} | {row.team_id for row, _ in past}
    )
    pending_attempts = read_covered_attempts(session, batch, pending_payouts, pending=True)
    pending = []
    for payout in pending_payouts:
        attempts = pending_attempts[payout.payout_id]
        issue = reconciliation_issue(payout, attempts)
        if issue is not None:
            raise EvaluationError(issue)
        pending.append(PendingObservation(payout, teams[payout.team_id].profession, attempts))

    historical_attempts = read_covered_attempts(
        session, batch, [payout for payout, _ in past], pending=False
    )
    bounds = repository.historical_collection_bounds(
        session, scope, {payout.team_id for payout, _ in past}, action_start, batch.cutoff
    )
    history = []
    for payout, disposition in sorted(
        past, key=lambda item: (item[0].created_at, item[0].payout_id)
    ):
        attempts = historical_attempts[payout.payout_id]
        first_day, last_day = bounds.get(payout.team_id, (None, None))
        complete = reconciliation_issue(payout, attempts) is None and within_collection_bounds(
            payout.covers_from, payout.covers_to, first_day, last_day
        )
        history.append(
            HistoricalObservation(
                payout,
                teams[payout.team_id].profession,
                attempts,
                disposition,
                complete,
                historical_eligibility(disposition, batch.cutoff, snapshot_at, complete),
            )
        )
    return EvaluationInputs(
        batch,
        snapshot_at,
        config,
        tuple(teams[key] for key in sorted(teams)),
        tuple(pending),
        exclusions,
        tuple(history),
        tuple(row for row in history if is_fraud_seed(row.disposition)),
        monitoring_input(batch.payout_date, tuple(history)),
    )


def capture_inputs(
    database: Database,
    actor: ActorContext,
    batch_id: UUID,
    config: EvaluationConfig | None = None,
) -> EvaluationInputs:
    scope = evaluation_scope(database, actor)
    if not isinstance(batch_id, UUID):
        raise EvaluationError("invalid_dataset_batch_id")
    try:
        with database.session() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            session.execute(text("SET TRANSACTION READ ONLY"))
            snapshot_at = session.scalar(select(func.clock_timestamp()))
            return materialize(session, scope, batch_id, snapshot_at, config or EvaluationConfig())
    except (OperationalError, PoolTimeout):
        raise EvaluationError("evaluation_storage_unavailable", 503) from None
