from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, aliased

from app.models import (
    EvaluationRun,
    FraudCluster,
    Payout,
    PayoutDecision,
    PayoutDecisionHistory,
    User,
)
from app.models.values import Decision, EvaluationStatus


def completed_decision_filters(run_id: UUID, dataset_scope: str) -> tuple[ColumnElement[bool], ...]:
    return (
        PayoutDecision.run_id == run_id,
        EvaluationRun.scope_key == dataset_scope,
        EvaluationRun.status == EvaluationStatus.COMPLETED,
    )


def effective_decisions(
    run_id: UUID, dataset_scope: str, review_scope: str, *, decision: Decision | None = None
) -> Select[tuple[PayoutDecision, PayoutDecisionHistory, str]]:
    """Select original, latest scoped event, and its actor in one PostgreSQL statement."""
    latest = aliased(PayoutDecisionHistory)
    latest_id = (
        select(PayoutDecisionHistory.history_id)
        .where(
            PayoutDecisionHistory.decision_id == PayoutDecision.decision_id,
            PayoutDecisionHistory.scope_key == review_scope,
        )
        .order_by(PayoutDecisionHistory.version.desc())
        .limit(1)
        .correlate(PayoutDecision)
        .scalar_subquery()
    )
    query = (
        select(PayoutDecision, latest, User.display_name)
        .join(EvaluationRun, EvaluationRun.run_id == PayoutDecision.run_id)
        .outerjoin(latest, latest.history_id == latest_id)
        .outerjoin(User, User.user_id == latest.changed_by)
        .where(*completed_decision_filters(run_id, dataset_scope))
    )
    if decision is not None:
        query = query.where(func.coalesce(latest.new_decision, PayoutDecision.decision) == decision)
    return query


def lock_original(
    session: Session, run_id: UUID, payout_id: str, dataset_scope: str
) -> tuple[PayoutDecision, dict[str, Any], str] | None:
    row = session.execute(
        select(PayoutDecision, EvaluationRun.history_manifest, Payout.team_id)
        .join(EvaluationRun, EvaluationRun.run_id == PayoutDecision.run_id)
        .join(Payout, Payout.payout_id == PayoutDecision.payout_id)
        .where(
            *completed_decision_filters(run_id, dataset_scope),
            PayoutDecision.payout_id == payout_id,
        )
        .with_for_update(of=PayoutDecision)
    ).one_or_none()
    return (row[0], row[1], row[2]) if row is not None else None


def scoped_events(
    session: Session, decision_id: UUID, review_scope: str, through: int
) -> Iterable[tuple[PayoutDecisionHistory, str]]:
    return session.execute(
        select(PayoutDecisionHistory, User.display_name)
        .join(User, User.user_id == PayoutDecisionHistory.changed_by)
        .where(
            PayoutDecisionHistory.decision_id == decision_id,
            PayoutDecisionHistory.scope_key == review_scope,
            PayoutDecisionHistory.version <= through,
        )
        .order_by(PayoutDecisionHistory.version)
    ).tuples()


def run_cluster(session: Session, run_id: UUID, cluster_id: UUID) -> FraudCluster | None:
    return session.scalar(
        select(FraudCluster).where(
            FraudCluster.run_id == run_id,
            FraudCluster.cluster_id == cluster_id,
        )
    )


def append_override(session: Session, **values: Any) -> None:
    session.add(PayoutDecisionHistory(**values, created_at=func.clock_timestamp()))
    session.flush()
