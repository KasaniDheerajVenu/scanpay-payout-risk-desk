from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import EvaluationRun
from app.models.values import EvaluationStatus


def lock_fingerprint(session: Session, batch_id: UUID, digest: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"scanpay-evaluation:{batch_id}:{digest}"},
    )


def reusable_run(session: Session, scope: str, batch_id: UUID, digest: str) -> EvaluationRun | None:
    return session.scalar(
        select(EvaluationRun)
        .where(
            EvaluationRun.scope_key == scope,
            EvaluationRun.batch_id == batch_id,
            EvaluationRun.input_digest == digest,
            EvaluationRun.status.in_(
                [EvaluationStatus.QUEUED, EvaluationStatus.RUNNING, EvaluationStatus.COMPLETED]
            ),
        )
        .order_by(EvaluationRun.created_at, EvaluationRun.run_id)
        .limit(1)
        .with_for_update()
    )


def lock_run(session: Session, scope: str, run_id: UUID) -> EvaluationRun | None:
    return session.scalar(
        select(EvaluationRun)
        .where(EvaluationRun.scope_key == scope, EvaluationRun.run_id == run_id)
        .with_for_update()
    )
