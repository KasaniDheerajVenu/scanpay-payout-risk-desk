from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, FiniteFloat

from app.models.values import MLStatus
from app.risk_engine.feature_schema import Availability, Direction, Feature, Unit
from app.schemas.fraud_findings import EvidenceRef
from app.schemas.risk_findings import BaselineScope


class MLRecord(BaseModel):
    model_config = ConfigDict(frozen=True)


class GateResult(MLRecord):
    code: str
    passed: bool
    observed: FiniteFloat | int | None = None
    limit: FiniteFloat | int


class ValidationMetrics(MLRecord):
    history_fit_candidates: int = 0
    history_validation_only: int = 0
    training_candidates: int = 0
    training_fitted: int = 0
    calibration_candidates: int = 0
    calibration_scored: int = 0
    validation_candidates: int = 0
    validation_scored: int = 0
    validation_anomaly_count: int | None = None
    validation_anomaly_rate: FiniteFloat | None = None
    normal_history_scored: int | None = None
    normal_history_alerts: int | None = None
    normal_history_alert_rate: FiniteFloat | None = None
    verified_outliers_scored: int | None = None
    verified_outlier_alerts: int | None = None
    victim_observations_scored: int | None = None
    victim_alerts: int | None = None
    statistical_review_count: int | None = None
    statistical_review_rate: FiniteFloat | None = None
    assisted_review_count: int | None = None
    assisted_review_rate: FiniteFloat | None = None
    actionable_alerts: int | None = None
    incremental_review_count: int | None = None
    incremental_review_rate: FiniteFloat | None = None
    flagged_exposure_cents: int | None = None
    incremental_review_exposure_cents: int | None = None
    fit_attempted: bool = False


class ModelVersionDraft(MLRecord):
    scope_key: str
    training_fingerprint: str
    algorithm: str
    feature_schema_version: str
    config: dict[str, Any]
    fit_cutoff_at: datetime | None
    validation_cutoff_at: datetime | None
    training_observation_count: int
    validation_observation_count: int
    anomaly_threshold: FiniteFloat | None
    status: MLStatus
    metrics: ValidationMetrics
    retained_features: tuple[str, ...] = ()
    quality_gates: tuple[GateResult, ...] = ()
    diagnostics: tuple[str, ...] = ()
    calibration_cutoff_at: datetime | None = None

    def persistence_fields(self) -> dict[str, Any]:
        values = self.model_dump(
            exclude={
                "metrics",
                "retained_features",
                "quality_gates",
                "diagnostics",
                "calibration_cutoff_at",
            }
        )
        metadata = self.model_dump(
            mode="json",
            include={
                "metrics",
                "retained_features",
                "quality_gates",
                "diagnostics",
                "calibration_cutoff_at",
            },
        )
        values["metrics"] = {**metadata.pop("metrics"), **metadata}
        return values


class FeatureAvailability(MLRecord):
    feature: str
    availability: Availability


class UnusualFeature(MLRecord):
    feature: Feature
    family: str
    scope: BaselineScope
    unit: Unit
    observed: Decimal | int
    historical_median: Decimal
    direction: Direction
    baseline_sample_count: int
    evidence: tuple[EvidenceRef, ...]
    comparison: str


class MLFinding(MLRecord):
    payout_id: str
    training_fingerprint: str
    feature_schema_version: str
    status: MLStatus
    anomaly_score: FiniteFloat | None = None
    anomaly_threshold: FiniteFloat | None = None
    is_anomaly: bool | None = None
    observed_model_features: int
    retained_model_features: int
    feature_availability: tuple[FeatureAvailability, ...]
    unusual_features: tuple[UnusualFeature, ...] = ()
    evidence: tuple[EvidenceRef, ...]
    qualifies_for_review: bool = False
    diagnostic_code: str | None = None
    explanation: str
