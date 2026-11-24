from datetime import date, datetime
from itertools import batched

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models import DatasetBatch, Payout, Team, Transaction
from app.models.values import TransactionStatus

type SourceModel = Team | Transaction | Payout
type TeamDay = tuple[str, date]


def find_sources[Model: SourceModel](
    session: Session, model: type[Model], key: InstrumentedAttribute[str], ids: set[str]
) -> dict[str, tuple[Model, str]]:
    records: dict[str, tuple[Model, str]] = {}
    for page in batched(sorted(ids), 1000):
        statement = (
            select(model, DatasetBatch.scope_key)
            .join(DatasetBatch, model.first_import_batch_id == DatasetBatch.batch_id)
            .where(key.in_(page))
        )
        for record, scope in session.execute(statement):
            records[getattr(record, key.key)] = record, scope
    return records


def payouts_by_settlement(
    session: Session, scope: str, settlements: set[TeamDay]
) -> dict[TeamDay, list[Payout]]:
    payouts: dict[TeamDay, list[Payout]] = {}
    for page in batched(sorted(settlements), 1000):
        statement = (
            select(Payout)
            .join(DatasetBatch, Payout.first_import_batch_id == DatasetBatch.batch_id)
            .where(
                DatasetBatch.scope_key == scope,
                tuple_(Payout.team_id, Payout.created_date_ist).in_(page),
            )
        )
        for payout in session.scalars(statement):
            payouts.setdefault((payout.team_id, payout.created_date_ist), []).append(payout)
    return payouts


def captured_by_collection_date(
    session: Session, scope: str, dates: set[TeamDay], cutoff: datetime
) -> dict[TeamDay, tuple[int, int]]:
    captured: dict[TeamDay, tuple[int, int]] = {}
    for page in batched(sorted(dates), 1000):
        statement = (
            select(
                Transaction.team_id,
                Transaction.collection_date_ist,
                func.count(),
                func.sum(Transaction.amount_cents + Transaction.tip_cents),
            )
            .join(DatasetBatch, Transaction.first_import_batch_id == DatasetBatch.batch_id)
            .where(
                DatasetBatch.scope_key == scope,
                Transaction.status == TransactionStatus.CAPTURED,
                Transaction.created_at <= cutoff,
                tuple_(Transaction.team_id, Transaction.collection_date_ist).in_(page),
            )
            .group_by(Transaction.team_id, Transaction.collection_date_ist)
        )
        for team_id, collection_date, count, cents in session.execute(statement):
            captured[team_id, collection_date] = count, int(cents)
    return captured


def collection_bounds(
    session: Session, scope: str, cutoff: datetime
) -> tuple[date | None, date | None]:
    statement = (
        select(func.min(Transaction.collection_date_ist), func.max(Transaction.collection_date_ist))
        .join(DatasetBatch, Transaction.first_import_batch_id == DatasetBatch.batch_id)
        .where(DatasetBatch.scope_key == scope, Transaction.created_at <= cutoff)
    )
    first, last = session.execute(statement).one()
    return first, last
