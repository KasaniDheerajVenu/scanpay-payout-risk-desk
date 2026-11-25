from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, defer, load_only

from app.models import (
    DatasetBatch,
    EvaluationRun,
    FraudCluster,
    ModelVersion,
    Payout,
    PayoutDecision,
    Team,
    Transaction,
)
from app.repositories.payout_decisions import effective_decisions
from app.schemas.evaluation import EvaluationError
from app.schemas.risk_results import CoveredTransaction, PayoutQuery

SORT_COLUMNS = {
    "payout_id": PayoutDecision.payout_id,
    "amount": Payout.payout_amount_cents,
    "team_name": Team.team_name,
    "created_at": Payout.created_at,
}

PAYOUT_COLUMNS = load_only(
    Payout.payout_id,
    Payout.team_id,
    Payout.payout_amount_cents,
    Payout.txn_count,
    Payout.covers_from,
    Payout.covers_to,
    Payout.created_at,
    raiseload=True,
)
TEAM_COLUMNS = load_only(
    Team.team_id,
    Team.team_name,
    Team.profession,
    Team.status,
    raiseload=True,
)


def runs(scope: str):
    return (
        select(EvaluationRun, ModelVersion, DatasetBatch.mode, DatasetBatch.imported_at)
        .join(DatasetBatch, EvaluationRun.batch_id == DatasetBatch.batch_id)
        .outerjoin(
            ModelVersion,
            (ModelVersion.model_version_id == EvaluationRun.model_version_id)
            & (ModelVersion.scope_key == scope),
        )
        .where(EvaluationRun.scope_key == scope, DatasetBatch.scope_key == scope)
        .options(defer(EvaluationRun.history_manifest, raiseload=True))
    )


def models(session: Session, scope: str, limit: int, offset: int):
    return session.scalars(
        select(ModelVersion)
        .where(ModelVersion.scope_key == scope)
        .order_by(ModelVersion.created_at.desc(), ModelVersion.model_version_id)
        .limit(limit)
        .offset(offset)
    )


def payout_context(query):
    return (
        query.add_columns(Payout, Team)
        .join(Payout, Payout.payout_id == PayoutDecision.payout_id)
        .join(Team, Team.team_id == Payout.team_id)
    )


def payout_page(session: Session, dataset: str, review: str, filters: PayoutQuery):
    query = payout_context(
        effective_decisions(filters.run_id, dataset, review, decision=filters.decision)
    )
    if filters.search:
        query = query.where(
            or_(
                *(
                    column.icontains(filters.search, autoescape=True)
                    for column in (
                        PayoutDecision.payout_id,
                        Team.team_id,
                        Team.team_name,
                        Team.profession,
                    )
                )
            )
        )
    total = session.scalar(
        select(func.count()).select_from(
            query.with_only_columns(PayoutDecision.decision_id).subquery()
        )
    )
    column = SORT_COLUMNS[filters.sort]
    ordering = column.desc() if filters.direction == "desc" else column.asc()
    rows = session.execute(
        query.options(PAYOUT_COLUMNS, TEAM_COLUMNS, defer(PayoutDecision.findings, raiseload=True))
        .order_by(ordering, PayoutDecision.payout_id)
        .limit(filters.limit)
        .offset(filters.offset)
    ).all()
    return rows, total


def result_amounts(session: Session, run_id: UUID, dataset: str, review: str):
    return session.execute(
        effective_decisions(run_id, dataset, review)
        .add_columns(Payout.team_id, Payout.payout_amount_cents)
        .join(Payout, Payout.payout_id == PayoutDecision.payout_id)
        .options(defer(PayoutDecision.findings, raiseload=True))
        .order_by(PayoutDecision.payout_id)
        .execution_options(yield_per=500)
    )


def payout_detail(session: Session, run_id: UUID, dataset: str, review: str, payout_id: str):
    return session.execute(
        payout_context(effective_decisions(run_id, dataset, review))
        .where(PayoutDecision.payout_id == payout_id)
        .options(PAYOUT_COLUMNS, TEAM_COLUMNS)
    ).one_or_none()


def covered_transactions(
    session: Session, run_id: UUID, payout_id: str, team_id: str
) -> tuple[CoveredTransaction, ...]:
    manifest = session.scalar(
        select(EvaluationRun.history_manifest).where(EvaluationRun.run_id == run_id)
    )
    ids = next(
        (
            row["attempt_ids"]
            for row in (manifest or {}).get("selected_pending", ())
            if row["payout_id"] == payout_id
        ),
        None,
    )
    if ids is None:
        raise EvaluationError("missing_run_payout_snapshot", 503)
    if not ids:
        return ()
    rows = session.execute(
        select(
            Transaction.txn_id,
            Transaction.created_at,
            Transaction.collection_date_ist,
            Transaction.amount_cents,
            Transaction.tip_cents,
            Transaction.payment_method,
            Transaction.status,
        )
        .where(Transaction.txn_id.in_(ids), Transaction.team_id == team_id)
        .order_by(Transaction.created_at, Transaction.txn_id)
    ).mappings()
    summaries = tuple(CoveredTransaction.model_validate(row) for row in rows)
    if len(summaries) != len(ids):
        raise EvaluationError("missing_run_payout_snapshot", 503)
    return summaries


def clusters(session: Session, run_id: UUID):
    return session.scalars(
        select(FraudCluster)
        .where(FraudCluster.run_id == run_id)
        .order_by(FraudCluster.stable_case_key)
    ).all()


def historical_case_reasons(session: Session, payout_ids: set[str]) -> dict[str, str]:
    if not payout_ids:
        return {}
    return dict(
        session.execute(
            select(Payout.payout_id, Payout.historical_reason_code).where(
                Payout.payout_id.in_(payout_ids),
                Payout.historical_reason_code.is_not(None),
            )
        ).all()
    )
