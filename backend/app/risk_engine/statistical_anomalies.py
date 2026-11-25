from decimal import Decimal, localcontext

from app.risk_engine.feature_schema import (
    ARITHMETIC,
    DEFINITIONS,
    FEATURE_VERSION,
    Direction,
    FeatureRule,
    StatisticalPolicy,
    Unit,
)
from app.risk_engine.features import FeatureObservation
from app.schemas.risk_findings import (
    Baseline,
    BaselineScope,
    ComparisonStatus,
    Reliability,
    StatisticalFinding,
)


def format_value(value: Decimal | int, unit: Unit) -> str:
    if unit == Unit.CENTS:
        return f"${Decimal(value) / 100:,.2f}"
    if unit == Unit.RATIO:
        return f"{Decimal(value):.2%}"
    return format(value, "f") if isinstance(value, Decimal) else str(value)


def compare(
    subject: FeatureObservation, baseline: Baseline, rule: FeatureRule, policy: StatisticalPolicy
) -> StatisticalFinding:
    item = subject.get(rule.feature)
    label, unit = DEFINITIONS[rule.feature]
    scope_label = "the team's comparable history"
    if baseline.scope == BaselineScope.PROFESSION:
        scope_label = "comparable payouts from other teams in the same profession"
    status = ComparisonStatus.OBSERVATION_UNAVAILABLE
    explanation = f"{label} is {item.availability.value.replace('_', ' ')} for this payout."
    low = high = absolute = relative = direction = material = None
    with localcontext(ARITHMETIC):
        if item.value is not None and baseline.reliability != Reliability.AVAILABLE:
            status = ComparisonStatus.INSUFFICIENT_HISTORY
            explanation = (
                f"{label} has {baseline.sample_count} eligible observations in {scope_label}. "
                "There is insufficient comparable history for a reliable range."
            )
        elif item.value is not None:
            assert (
                baseline.q1 is not None
                and baseline.q3 is not None
                and baseline.median is not None
                and baseline.iqr is not None
            )
            value = Decimal(item.value)
            delta = value - baseline.median
            absolute = abs(delta)
            relative = absolute / abs(baseline.median) if baseline.median else None
            spread = policy.iqr_multiplier * baseline.iqr
            if rule.direction in {Direction.BOTH, Direction.LOW}:
                low = baseline.q1 - spread
            if rule.direction in {Direction.BOTH, Direction.HIGH}:
                high = baseline.q3 + spread
            if high is not None and value > high:
                direction, fence, position = Direction.HIGH, high, "above"
            elif low is not None and value < low:
                direction, fence, position = Direction.LOW, low, "below"
            else:
                fence, position = None, ""
            if direction is not None:
                material = absolute >= rule.minimum_absolute and (
                    relative is None or relative >= rule.minimum_relative
                )
                status = ComparisonStatus.ANOMALY if material else ComparisonStatus.NONMATERIAL
                explanation = (
                    f"{label} is {format_value(value, unit)}, {position} the "
                    f"{format_value(fence, unit)} fence for {scope_label} "
                    f"(median {format_value(baseline.median, unit)}). "
                )
                if material:
                    explanation += "Review the supporting payments and their business context."
                else:
                    explanation += "The deviation is below the materiality threshold."
                if relative is None:
                    explanation += " The zero median leaves relative deviation unavailable."
            elif rule.direction == Direction.DIAGNOSTIC:
                status = ComparisonStatus.DIAGNOSTIC
                explanation = (
                    f"{label} is {format_value(value, unit)}. The median for {scope_label} "
                    f"is {format_value(baseline.median, unit)}."
                )
            else:
                status = ComparisonStatus.IN_RANGE
                explanation = f"{label} is within the configured range for {scope_label}."

    return StatisticalFinding(
        feature=rule.feature,
        feature_version=FEATURE_VERSION,
        policy_version=policy.version,
        unit=unit,
        observed=item.value,
        observation_availability=item.availability,
        observed_denominator=item.denominator,
        baseline=baseline,
        status=status,
        direction=direction,
        configured_direction=rule.direction,
        lower_fence=low,
        upper_fence=high,
        iqr_multiplier=policy.iqr_multiplier,
        minimum_absolute=rule.minimum_absolute,
        minimum_relative=rule.minimum_relative,
        minimum_denominator=rule.minimum_denominator,
        absolute_deviation=absolute,
        relative_deviation=relative,
        is_material=material,
        current_payout_id=subject.payout_id,
        current_transaction_ids=item.transaction_ids,
        explanation=explanation,
    )
