from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.core.review_scope import ActorContext
from app.database.session import Database
from app.ml.config import ALGORITHM, MLPolicy, ml_config, model_configuration
from app.ml.inference import infer
from app.ml.training import (
    FittedModel,
    ModelError,
    Split,
    chronological_split,
    fit_model,
    prepare_fit,
    training_fingerprint,
)
from app.ml.validation import calibrate, gate, score_history, validation_gates
from app.models.values import MLStatus
from app.risk_engine.feature_schema import FEATURE_VERSION, StatisticalPolicy
from app.risk_engine.fraud_evidence import CaseMapping
from app.risk_engine.inputs import EvaluationConfig, EvaluationInputs
from app.schemas.ml_findings import GateResult, MLFinding, ModelVersionDraft, ValidationMetrics
from app.services.evaluation_input_service import capture_inputs
from app.services.fraud_analysis_service import (
    FraudAnalysis,
    analyze_fraud_and_statistics,
    fraud_config,
)
from app.services.statistical_analysis_service import StatisticalAnalysis, evaluation_config


@dataclass(frozen=True, slots=True)
class MLAnalysis:
    model: ModelVersionDraft
    pending: tuple[MLFinding, ...]


@dataclass(frozen=True, slots=True)
class EvaluationAnalysis:
    fraud: FraudAnalysis
    statistics: StatisticalAnalysis
    ml: MLAnalysis


@dataclass(frozen=True, slots=True)
class TrainingOutcome:
    status: MLStatus
    metrics: ValidationMetrics
    model: FittedModel | None = None
    threshold: float | None = None
    gates: tuple[GateResult, ...] = ()
    diagnostics: tuple[str, ...] = ()


def train_and_validate(split: Split, policy: MLPolicy) -> TrainingOutcome:
    metrics = ValidationMetrics(
        history_fit_candidates=split.fit_candidates,
        history_validation_only=split.validation_only,
        training_candidates=len(split.training),
        calibration_candidates=len(split.calibration),
        validation_candidates=len(split.validation),
    )
    if len(split.training) < policy.min_training:
        return TrainingOutcome(
            MLStatus.INSUFFICIENT_HISTORY,
            metrics,
            gates=(gate("training_candidates", len(split.training), policy.min_training),),
            diagnostics=("insufficient_training_history",),
        )
    gates = ()
    try:
        preprocessing, rows = prepare_fit(split.training, policy)
        gates = (
            gate("training_observations", len(rows), policy.min_training),
            gate("retained_features", len(preprocessing.columns), policy.min_features),
        )
        if not all(item.passed for item in gates):
            return TrainingOutcome(
                MLStatus.INSUFFICIENT_HISTORY,
                metrics,
                gates=gates,
                diagnostics=tuple(item.code for item in gates if not item.passed),
            )
        metrics = metrics.model_copy(update={"fit_attempted": True})
        model = fit_model(preprocessing, rows, policy)
        metrics = metrics.model_copy(update={"training_fitted": len(rows)})
    except ModelError:
        return TrainingOutcome(
            MLStatus.VALIDATION_FAILED,
            metrics,
            gates=gates,
            diagnostics=("invalid_training_input_or_fit_failure",),
        )

    threshold = None
    try:
        calibration = score_history(split.calibration, model)
        metrics = metrics.model_copy(update={"calibration_scored": len(calibration)})
        threshold = calibrate(calibration, policy)
        holdout = score_history(split.validation, model)
        metrics, validation_checks = validation_gates(
            model, split, policy, metrics, calibration, holdout, threshold
        )
        gates += validation_checks
    except ModelError:
        return TrainingOutcome(
            MLStatus.VALIDATION_FAILED,
            metrics,
            model,
            threshold,
            gates,
            ("validation_scoring_failed",),
        )
    diagnostics = tuple(item.code for item in gates if not item.passed)
    status = MLStatus.DIAGNOSTIC_ONLY if diagnostics else MLStatus.ACTIVE
    return TrainingOutcome(status, metrics, model, threshold, gates, diagnostics)


def analyze_ml(
    statistics: StatisticalAnalysis,
    config: EvaluationConfig,
    scope_key: str,
    cutoff: datetime,
    snapshot_at: datetime,
    policy: MLPolicy,
) -> MLAnalysis:
    config = ml_config(config, policy)
    model_config = {**model_configuration(policy), "evaluation_versions": config.version_metadata()}
    pending_ids = frozenset(row.observation.payout_id for row in statistics.pending)
    split = chronological_split(statistics.history, pending_ids, cutoff, snapshot_at, policy)
    fingerprint = training_fingerprint(split, model_config, cutoff)
    outcome = train_and_validate(split, policy)
    draft = ModelVersionDraft(
        scope_key=scope_key,
        training_fingerprint=fingerprint,
        algorithm=ALGORITHM,
        feature_schema_version=FEATURE_VERSION,
        config=model_config,
        fit_cutoff_at=split.fit_cutoff,
        calibration_cutoff_at=split.calibration_cutoff,
        validation_cutoff_at=cutoff,
        training_observation_count=outcome.metrics.training_fitted,
        validation_observation_count=outcome.metrics.validation_scored,
        anomaly_threshold=outcome.threshold,
        status=outcome.status,
        metrics=outcome.metrics,
        retained_features=outcome.model.preprocessing.names if outcome.model else (),
        quality_gates=outcome.gates,
        diagnostics=outcome.diagnostics,
    )
    pending = tuple(infer(row, outcome.model, draft, policy) for row in statistics.pending)
    return MLAnalysis(draft, pending)


def analyze_evaluation(
    inputs: EvaluationInputs,
    ml_policy: MLPolicy | None = None,
    statistical_policy: StatisticalPolicy | None = None,
    mapping: CaseMapping | None = None,
) -> EvaluationAnalysis:
    ml_policy = ml_policy or MLPolicy()
    statistical_policy = statistical_policy or StatisticalPolicy()
    statistics_config = evaluation_config(statistical_policy, inputs.config)
    if inputs.config.feature_version not in (None, FEATURE_VERSION) or (
        inputs.config.statistics_digest is not None and inputs.config != statistics_config
    ):
        raise ValueError("statistical_policy_fingerprint_mismatch")
    config = ml_config(fraud_config(statistics_config, mapping), ml_policy)
    result = analyze_fraud_and_statistics(
        replace(inputs, config=config), statistical_policy, mapping
    )
    ml = analyze_ml(
        result.statistics,
        config,
        inputs.batch.scope_key,
        inputs.batch.cutoff,
        inputs.snapshot_at,
        ml_policy,
    )
    return EvaluationAnalysis(result.fraud, result.statistics, ml)


def capture_ml_analysis(
    database: Database,
    actor: ActorContext,
    batch_id: UUID,
    ml_policy: MLPolicy | None = None,
    statistical_policy: StatisticalPolicy | None = None,
    mapping: CaseMapping | None = None,
) -> EvaluationAnalysis:
    ml_policy = ml_policy or MLPolicy()
    statistical_policy = statistical_policy or StatisticalPolicy()
    config = ml_config(fraud_config(evaluation_config(statistical_policy), mapping), ml_policy)
    inputs = capture_inputs(database, actor, batch_id, config)
    return analyze_evaluation(inputs, ml_policy, statistical_policy, mapping)
