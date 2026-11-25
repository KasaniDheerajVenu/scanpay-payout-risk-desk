from dataclasses import dataclass

import numpy as np

from app.ml.config import MLPolicy
from app.ml.inference import actionable, material_context
from app.ml.training import FittedModel, Split
from app.models.values import ReasonCode
from app.risk_engine.feature_schema import Feature
from app.schemas.ml_findings import GateResult, ValidationMetrics
from app.schemas.risk_findings import ComparisonStatus
from app.services.statistical_analysis_service import HistoricalFeatureRow, HistoryUse


@dataclass(frozen=True, slots=True)
class ScoredRow:
    row: HistoricalFeatureRow
    score: float


def score_history(
    rows: tuple[HistoricalFeatureRow, ...], model: FittedModel
) -> tuple[ScoredRow, ...]:
    return tuple(
        ScoredRow(row, score)
        for row in rows
        if (score := model.score(row.analysis.vector)[0]) is not None
    )


def calibrate(scores: tuple[ScoredRow, ...], policy: MLPolicy) -> float | None:
    if len(scores) < policy.min_calibration:
        return None
    return float(
        np.quantile([row.score for row in scores], policy.calibration_quantile, method="higher")
    )


def gate(code: str, observed: float | None, limit: float, *, minimum: bool = True) -> GateResult:
    passed = observed is not None and (observed >= limit if minimum else observed <= limit)
    return GateResult(code=code, passed=passed, observed=observed, limit=limit)


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def validation_gates(
    model: FittedModel,
    split: Split,
    policy: MLPolicy,
    metrics: ValidationMetrics,
    calibration: tuple[ScoredRow, ...],
    holdout: tuple[ScoredRow, ...],
    threshold: float | None,
) -> tuple[ValidationMetrics, tuple[GateResult, ...]]:
    counts = {
        "normal_history_alerts": 0,
        "verified_outliers_alerts": 0,
        "victim_observations_alerts": 0,
    }
    denominators: dict[str, int] = dict.fromkeys(counts, 0)
    anomalies = actionable_count = statistical = incremental = flagged_cents = incremental_cents = 0
    for row in holdout:
        history, analysis = row.row, row.row.analysis
        if history.use == HistoryUse.FIT_CANDIDATE:
            group = "normal_history_alerts"
        elif history.reason_code == ReasonCode.CARD_TESTING_VICTIM:
            group = "victim_observations_alerts"
        else:
            group = "verified_outliers_alerts"
        denominators[group] += 1
        statistical_signal = any(
            item.status == ComparisonStatus.ANOMALY for item in analysis.findings
        )
        statistical += statistical_signal
        if threshold is None or row.score <= threshold:
            continue
        cents = int(analysis.observation.get(Feature.PAYOUT_CENTS).value or 0)
        counts[group] += 1
        anomalies += 1
        flagged_cents += cents
        if actionable(material_context(analysis, model.preprocessing.names), policy):
            actionable_count += 1
            if not statistical_signal:
                incremental += 1
                incremental_cents += cents

    n = len(holdout)
    normal = denominators["normal_history_alerts"]
    rates = (
        {
            "validation_anomaly_rate": ratio(anomalies, n),
            "normal_history_alert_rate": ratio(counts["normal_history_alerts"], normal),
            "incremental_review_rate": ratio(incremental, n),
        }
        if threshold is not None
        else {}
    )
    updates = dict(
        calibration_scored=len(calibration),
        validation_scored=n,
        normal_history_scored=normal,
        verified_outliers_scored=denominators["verified_outliers_alerts"],
        victim_observations_scored=denominators["victim_observations_alerts"],
        statistical_review_count=statistical,
        statistical_review_rate=ratio(statistical, n),
    )
    if threshold is not None:
        updates.update(
            validation_anomaly_count=anomalies,
            normal_history_alerts=counts["normal_history_alerts"],
            verified_outlier_alerts=counts["verified_outliers_alerts"],
            victim_alerts=counts["victim_observations_alerts"],
            assisted_review_count=statistical + incremental,
            assisted_review_rate=ratio(statistical + incremental, n),
            actionable_alerts=actionable_count,
            incremental_review_count=incremental,
            flagged_exposure_cents=flagged_cents,
            incremental_review_exposure_cents=incremental_cents,
            **rates,
        )
    metrics = metrics.model_copy(update=updates)
    gates = (
        gate("calibration_observations", len(calibration), policy.min_calibration),
        gate("validation_observations", n, policy.min_validation),
        gate(
            "validation_feature_coverage",
            ratio(n, len(split.validation)),
            policy.min_validation_coverage,
        ),
        gate(
            "validation_anomaly_workload",
            metrics.validation_anomaly_rate,
            policy.max_validation_anomaly_rate,
            minimum=False,
        ),
        gate(
            "apparent_normal_workload",
            metrics.normal_history_alert_rate,
            policy.max_normal_alert_rate,
            minimum=False,
        ),
        gate(
            "incremental_review_workload",
            metrics.incremental_review_rate,
            policy.max_incremental_review_rate,
            minimum=False,
        ),
        gate("actionable_alerts", metrics.actionable_alerts, policy.min_actionable_alerts),
    )
    return metrics, gates
