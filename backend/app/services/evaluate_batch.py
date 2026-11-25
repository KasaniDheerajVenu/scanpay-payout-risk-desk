from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.review_scope import ActorContext
from app.database.session import Database
from app.ml.config import MLPolicy, ml_config
from app.models.values import EvaluationStatus
from app.risk_engine.feature_schema import StatisticalPolicy
from app.risk_engine.fraud_evidence import CaseMapping
from app.risk_engine.hybrid_decisions import RULE_VERSION
from app.risk_engine.inputs import EvaluationConfig
from app.schemas.evaluation import EvaluationError, RunFailure, RunSummary
from app.services.evaluation_publication import publish_evaluation
from app.services.evaluation_service import claim_run, fail_run, prepare_run
from app.services.fraud_analysis_service import fraud_config
from app.services.ml_analysis_service import analyze_evaluation
from app.services.statistical_analysis_service import evaluation_config


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    run: RunSummary
    created: bool


def hybrid_config(
    ml_policy: MLPolicy, statistical_policy: StatisticalPolicy, mapping: CaseMapping | None = None
) -> EvaluationConfig:
    return ml_config(
        fraud_config(
            evaluation_config(statistical_policy, EvaluationConfig(rule_version=RULE_VERSION)),
            mapping,
        ),
        ml_policy,
    )


def evaluate_batch(
    database: Database,
    actor: ActorContext,
    batch_id: UUID,
    *,
    ml_policy: MLPolicy | None = None,
    statistical_policy: StatisticalPolicy | None = None,
    mapping: CaseMapping | None = None,
) -> EvaluationResult:
    ml_policy = ml_policy or MLPolicy()
    statistical_policy = statistical_policy or StatisticalPolicy()
    config = hybrid_config(ml_policy, statistical_policy, mapping)
    prepared = prepare_run(database, actor, batch_id, config, recover_stale=True)
    if prepared.inputs is None:
        if prepared.run.status != EvaluationStatus.COMPLETED:
            raise EvaluationError("evaluation_in_progress", 409)
        return EvaluationResult(prepared.run, created=False)
    run = claim_run(database, actor, prepared.run.run_id)
    failure = RunFailure.PROCESSING_FAILED
    try:
        analysis = analyze_evaluation(prepared.inputs, ml_policy, statistical_policy, mapping)
        failure = RunFailure.PUBLICATION_FAILED
        completed = publish_evaluation(database, actor, run, prepared.inputs, analysis)
        return EvaluationResult(completed, created=True)
    except Exception as exc:
        try:
            fail_run(database, actor, run.run_id, failure)
        except (EvaluationError, SQLAlchemyError):
            # A terminal or unreachable row is resolved by replay or stale-attempt recovery.
            pass
        if isinstance(exc, EvaluationError):
            raise
        raise EvaluationError(failure.value, 503) from None
