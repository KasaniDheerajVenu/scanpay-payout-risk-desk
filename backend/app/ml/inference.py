from app.ml.config import MLPolicy
from app.ml.preprocessing import InvalidFeatures
from app.ml.training import FittedModel, ModelError
from app.models.values import MLStatus
from app.risk_engine.feature_schema import DEFINITIONS, Direction, Feature
from app.schemas.fraud_findings import EvidenceRef, EvidenceType
from app.schemas.ml_findings import (
    FeatureAvailability,
    MLFinding,
    ModelVersionDraft,
    UnusualFeature,
)
from app.schemas.risk_findings import BaselineScope, Reliability, StatisticalFinding
from app.services.statistical_analysis_service import ObservationAnalysis

FAMILIES = {
    Feature.PAYOUT_CENTS: "amount",
    Feature.MEDIAN_BASE_CENTS: "amount",
    Feature.MAX_BASE_CENTS: "amount",
    Feature.CAPTURED_COUNT: "volume",
    Feature.ATTEMPT_COUNT: "volume",
    Feature.PEAK_ATTEMPTS: "volume",
    Feature.DECLINE_RATE: "payment_outcomes",
    Feature.REFUND_RATE: "payment_outcomes",
    Feature.TIP_RATIO: "tipping",
    Feature.REPEATED_CARD_ATTEMPTS: "repetition",
}


def evidence_for(comparison: StatisticalFinding) -> tuple[EvidenceRef, ...]:
    baseline = comparison.baseline
    payouts = (comparison.current_payout_id, *baseline.payout_ids)
    transactions = (*comparison.current_transaction_ids, *baseline.transaction_ids)
    return (
        *(EvidenceRef(type=EvidenceType.PAYOUT, id=item) for item in dict.fromkeys(payouts)),
        *(
            EvidenceRef(type=EvidenceType.TRANSACTION, id=item)
            for item in dict.fromkeys(transactions)
        ),
    )


def material_context(
    analysis: ObservationAnalysis, retained_features: tuple[str, ...]
) -> tuple[UnusualFeature, ...]:
    observed_features = {
        name
        for name, value in zip(analysis.vector.names, analysis.vector.values, strict=True)
        if value is not None and name in retained_features
    }
    findings = []
    for pair in analysis.comparisons:
        family = FAMILIES.get(pair.team.feature)
        if family is None:
            continue
        for item in (pair.team, pair.profession):
            baseline = item.baseline
            relative_suffix = "team" if baseline.scope == BaselineScope.TEAM else "profession"
            if not {
                item.feature.value,
                f"{item.feature.value}_relative_{relative_suffix}",
            }.intersection(observed_features):
                continue
            if (
                baseline.reliability != Reliability.AVAILABLE
                or baseline.median is None
                or item.observed is None
                or item.absolute_deviation is None
                or item.absolute_deviation < item.minimum_absolute
                or (
                    item.relative_deviation is not None
                    and item.relative_deviation < item.minimum_relative
                )
                or not baseline.payout_ids
                or item.observed == baseline.median
                or (
                    item.observed_denominator is not None
                    and item.observed_denominator < item.minimum_denominator
                )
            ):
                continue
            direction = Direction.HIGH if item.observed > baseline.median else Direction.LOW
            if item.configured_direction not in (direction, Direction.BOTH):
                continue
            label = DEFINITIONS[item.feature].label
            unit = item.unit.value
            findings.append(
                UnusualFeature(
                    feature=item.feature,
                    family=family,
                    scope=baseline.scope,
                    unit=item.unit,
                    observed=item.observed,
                    historical_median=baseline.median,
                    direction=direction,
                    baseline_sample_count=baseline.sample_count,
                    evidence=evidence_for(item),
                    comparison=(
                        f"{label} was {item.observed} ({unit}) versus a comparable "
                        f"{baseline.scope.value.lower()} median of {baseline.median} ({unit})."
                    ),
                )
            )
            break
    return tuple(findings)


def actionable(context: tuple[UnusualFeature, ...], policy: MLPolicy) -> bool:
    return len({item.family for item in context}) >= policy.min_actionable_families


def infer(
    analysis: ObservationAnalysis,
    model: FittedModel | None,
    draft: ModelVersionDraft,
    policy: MLPolicy,
) -> MLFinding:
    payout_id = analysis.observation.payout_id
    features = tuple(
        FeatureAvailability(feature=name, availability=state)
        for name, state in zip(analysis.vector.names, analysis.vector.availability, strict=False)
    )
    status, score, count = draft.status, None, 0
    code = draft.diagnostics[0] if draft.diagnostics else None
    if model is not None:
        try:
            score, count = model.score(analysis.vector)
            if score is None:
                status, code = MLStatus.INFERENCE_FAILED, "insufficient_payout_features"
        except InvalidFeatures:
            status, code = MLStatus.INFERENCE_FAILED, "invalid_payout_features"
        except ModelError:
            status, code = MLStatus.INFERENCE_FAILED, "model_scoring_failed"
    threshold = draft.anomaly_threshold
    flagged = score > threshold if score is not None and threshold is not None else None
    context = material_context(analysis, draft.retained_features) if flagged else ()
    qualifies = status == MLStatus.ACTIVE and flagged is True and actionable(context, policy)
    evidence = dict.fromkeys(
        (
            EvidenceRef(type=EvidenceType.PAYOUT, id=payout_id),
            *(ref for item in context for ref in item.evidence),
        )
    )
    if qualifies:
        explanation = (
            "The score exceeded the historical anomaly threshold. Review the combined payment "
            "patterns in the supporting team or profession comparisons."
        )
    elif status != MLStatus.ACTIVE:
        explanation = "The model or payout did not meet the requirements for ML-assisted review."
    elif flagged:
        explanation = "The score exceeded the threshold with limited supporting review evidence."
    else:
        explanation = "The score remained within the calibrated historical threshold."
    return MLFinding(
        payout_id=payout_id,
        training_fingerprint=draft.training_fingerprint,
        feature_schema_version=draft.feature_schema_version,
        status=status,
        anomaly_score=score,
        anomaly_threshold=threshold,
        is_anomaly=flagged,
        observed_model_features=count,
        retained_model_features=len(draft.retained_features),
        feature_availability=features,
        unusual_features=context,
        evidence=tuple(evidence),
        qualifies_for_review=qualifies,
        diagnostic_code=code,
        explanation=explanation,
    )
