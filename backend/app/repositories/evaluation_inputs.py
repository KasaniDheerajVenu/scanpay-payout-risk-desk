from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from itertools import batched

from sqlalchemy import Select, and_, func, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.models import DatasetBatch, Payout, Team, Transaction
from app.models.values import BatchStatus, SourceKind
from app.repositories.sources import TeamDay


@dataclass(frozen=True, slots=True, repr=False)
class PayoutSource:
    payout: Payout
    imported_at: datetime
    disposition_available_at: datetime | None


type PayoutQuery = Select[tuple[Payout, datetime | None, datetime | None]]


def payout_query(scope: str) -> PayoutQuery:
    closure = aliased(DatasetBatch)
    return (
        select(Payout, DatasetBatch.imported_at, closure.imported_at)
        .join(DatasetBatch, Payout.first_import_batch_id == DatasetBatch.batch_id)
        .outerjoin(
            closure,
            and_(
                closure.batch_id == Payout.disposition_import_batch_id,
                closure.status == BatchStatus.READY,
                closure.scope_key == scope,
            ),
        )
        .where(
            DatasetBatch.scope_key == scope,
            DatasetBatch.status == BatchStatus.READY,
            DatasetBatch.imported_at.is_not(None),
        )
    )


def payout_sources(session: Session, statement: PayoutQuery) -> Iterator[PayoutSource]:
    for payout, imported_at, disposition_imported_at in session.execute(statement):
        assert imported_at is not None
        available = (
            imported_at
            if payout.first_source_kind == SourceKind.HISTORY
            else disposition_imported_at
        )
        yield PayoutSource(payout, imported_at, available)


def read_pending_payouts(session: Session, scope: str, ids: set[str]) -> Iterator[PayoutSource]:
    statement = payout_query(scope)
    for page in batched(sorted(ids), 1000):
        yield from payout_sources(session, statement.where(Payout.payout_id.in_(page)))


def read_historical_payouts(
    session: Session, scope: str, before: date, cutoff: datetime
) -> Iterator[PayoutSource]:
    statement = payout_query(scope).where(
        Payout.created_date_ist < before,
        Payout.historical_decision.is_not(None),
        Payout.historical_decided_at <= cutoff,
    )
    yield from payout_sources(session, statement)


def read_teams(session: Session, scope: str, ids: set[str]) -> Iterator[tuple[Team, datetime]]:
    for page in batched(sorted(ids), 1000):
        statement = (
            select(Team, DatasetBatch.imported_at)
            .join(DatasetBatch, Team.first_import_batch_id == DatasetBatch.batch_id)
            .where(
                DatasetBatch.scope_key == scope,
                DatasetBatch.status == BatchStatus.READY,
                Team.team_id.in_(page),
            )
        )
        yield from session.execute(statement).tuples()


def read_attempts(
    session: Session,
    scope: str,
    days: set[TeamDay],
    cutoff: datetime,
    accepted_by: datetime | None = None,
) -> Iterator[tuple[Transaction, datetime]]:
    for page in batched(sorted(days), 1000):
        statement = (
            select(Transaction, DatasetBatch.imported_at)
            .join(DatasetBatch, Transaction.first_import_batch_id == DatasetBatch.batch_id)
            .where(
                DatasetBatch.scope_key == scope,
                DatasetBatch.status == BatchStatus.READY,
                Transaction.created_at <= cutoff,
                tuple_(Transaction.team_id, Transaction.collection_date_ist).in_(page),
            )
        )
        if accepted_by is not None:
            statement = statement.where(DatasetBatch.imported_at <= accepted_by)
        yield from session.execute(statement).tuples()


def historical_collection_bounds(
    session: Session, scope: str, ids: set[str], before: date, cutoff: datetime
) -> dict[str, tuple[date, date]]:
    bounds = {}
    for page in batched(sorted(ids), 1000):
        statement = (
            select(
                Transaction.team_id,
                func.min(Transaction.collection_date_ist),
                func.max(Transaction.collection_date_ist),
            )
            .join(DatasetBatch, Transaction.first_import_batch_id == DatasetBatch.batch_id)
            .where(
                DatasetBatch.scope_key == scope,
                DatasetBatch.status == BatchStatus.READY,
                Transaction.team_id.in_(page),
                Transaction.collection_date_ist < before,
                Transaction.created_at <= cutoff,
            )
            .group_by(Transaction.team_id)
        )
        for team_id, first_day, last_day in session.execute(statement):
            bounds[team_id] = (first_day, last_day)
    return bounds
