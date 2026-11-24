from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models import EvaluationRun
from app.models.values import EvaluationStatus
from app.repositories.evaluation_runs import lock_fingerprint, lock_run, reusable_run
from app.risk_engine.inputs import EvaluationConfig, EvaluationInputs
from app.schemas.evaluation import EvaluationError, RunFailure, RunSummary
from app.services.evaluation_input_service import capture_inputs, evaluation_scope
from app.services.evaluation_manifest import fingerprint, history_manifest, monitoring_metadata


@dataclass(frozen=True, slots=True)
class RunPreparation:
    run: RunSummary
    inputs: EvaluationInputs | None

    @property
    def created(self) -> bool:
        return self.inputs is not None


@contextmanager
def storage(database: Database) -> Iterator[Session]:
    try:
        with database.session() as session:
            yield session
    except (OperationalError, PoolTimeout):
        raise EvaluationError("evaluation_storage_unavailable", 503) from None


def summary(run: EvaluationRun) -> RunSummary:
    return RunSummary(
        run.run_id,
        run.batch_id,
        run.requested_by,
        EvaluationStatus(run.status),
        run.cutoff_at,
        run.snapshot_at,
        run.input_digest,
        run.rule_version,
        run.created_at,
        run.started_at,
        run.completed_at,
    )


def prepare_run(
    database: Database,
    actor: ActorContext,
    batch_id: UUID,
    config: EvaluationConfig | None = None,
) -> RunPreparation:
    inputs = capture_inputs(database, actor, batch_id, config)
    digest = fingerprint(inputs)
    scope = inputs.batch.scope_key
    manifest = history_manifest(inputs)
    monitoring = monitoring_metadata(inputs.monitoring)
    with storage(database) as session:
        lock_fingerprint(session, batch_id, digest)
        existing = reusable_run(session, scope, batch_id, digest)
        if existing is not None:
            return RunPreparation(summary(existing), None)
        row = EvaluationRun(
            batch_id=batch_id,
            scope_key=scope,
            requested_by=actor.user_id,
            cutoff_at=inputs.batch.cutoff,
            snapshot_at=inputs.snapshot_at,
            input_digest=digest,
            rule_version=inputs.config.rule_version,
            status=EvaluationStatus.QUEUED,
            history_manifest=manifest,
            summary={"historical_monitoring": monitoring},
        )
        session.add(row)
        session.commit()
        return RunPreparation(summary(row), inputs)


def claim_run(database: Database, actor: ActorContext, run_id: UUID) -> RunSummary:
    scope = evaluation_scope(database, actor)
    with storage(database) as session:
        row = lock_run(session, scope, run_id)
        if row is None:
            raise EvaluationError("evaluation_run_not_found", 404)
        if row.status != EvaluationStatus.QUEUED:
            raise EvaluationError("evaluation_run_not_queued", 409)
        row.status = EvaluationStatus.RUNNING
        row.started_at = session.scalar(select(func.clock_timestamp()))
        session.commit()
        return summary(row)


def fail_run(
    database: Database, actor: ActorContext, run_id: UUID, failure: RunFailure
) -> RunSummary:
    scope = evaluation_scope(database, actor)
    if not isinstance(failure, RunFailure):
        raise EvaluationError("invalid_evaluation_failure")
    with storage(database) as session:
        row = lock_run(session, scope, run_id)
        if row is None:
            raise EvaluationError("evaluation_run_not_found", 404)
        if row.status not in {EvaluationStatus.QUEUED, EvaluationStatus.RUNNING}:
            raise EvaluationError("evaluation_run_terminal", 409)
        row.status = EvaluationStatus.FAILED
        row.completed_at = session.scalar(select(func.clock_timestamp()))
        row.error_summary = {"code": failure.value}
        session.commit()
        return summary(row)
