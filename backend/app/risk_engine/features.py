from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from statistics import median

from app.models.values import PaymentMethod, Profession, TransactionStatus
from app.risk_engine.feature_schema import ARITHMETIC, Availability, Feature, StatisticalPolicy
from app.risk_engine.inputs import Attempt, HistoricalObservation, PendingObservation

type Observation = PendingObservation | HistoricalObservation
type Number = int | Decimal
FEATURE_INDEX = {feature: index for index, feature in enumerate(Feature)}


@dataclass(frozen=True, slots=True)
class Coverage:
    days: int
    weekend_days: int


@dataclass(frozen=True, slots=True)
class Measurement:
    value: Number | None
    availability: Availability
    transaction_ids: tuple[str, ...] = ()
    denominator: int | None = None


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    payout_id: str
    team_id: str
    profession: Profession
    created_at: datetime
    covers_from: date
    covers_to: date
    coverage: Coverage
    measurements: tuple[Measurement, ...]

    def get(self, feature: Feature) -> Measurement:
        return self.measurements[FEATURE_INDEX[feature]]


def attempt_ids(attempts: tuple[Attempt, ...]) -> tuple[str, ...]:
    return tuple(sorted(row.txn_id for row in attempts))


def ratio(numerator: int, denominator: int, minimum: int, evidence: tuple[str, ...]) -> Measurement:
    if denominator == 0:
        return Measurement(None, Availability.INAPPLICABLE, evidence, denominator)
    if denominator < minimum:
        return Measurement(None, Availability.INSUFFICIENT_OBSERVATIONS, evidence, denominator)
    return Measurement(
        Decimal(numerator) / denominator, Availability.AVAILABLE, evidence, denominator
    )


def peak_window(attempts: tuple[Attempt, ...], minutes: int) -> tuple[Attempt, ...]:
    ordered = sorted(attempts, key=lambda row: (row.created_at, row.txn_id))
    width = timedelta(minutes=minutes)
    left = best_start = best_end = 0
    for right, attempt in enumerate(ordered):
        while attempt.created_at - ordered[left].created_at > width:
            left += 1
        if right + 1 - left > best_end - best_start:
            best_start, best_end = left, right + 1
    return tuple(ordered[best_start:best_end])


def card_measurements(card: tuple[Attempt, ...], minimum: int) -> dict[Feature, Measurement]:
    ids = attempt_ids(card)
    absent = Availability.UNAVAILABLE if card else Availability.INAPPLICABLE
    missing = Measurement(None, absent, ids, len(card))
    distinct = repeated = countries = dominant = missing
    fingerprints = [(item.card_fingerprint or "").strip() for item in card]
    country_values = [(item.card_country or "").strip().upper() for item in card]
    if card and all(fingerprints):
        frequency = Counter(fingerprints)
        repeated_ids = tuple(
            sorted(
                item.txn_id
                for item, fingerprint in zip(card, fingerprints, strict=True)
                if frequency[fingerprint] > 1
            )
        )
        distinct = Measurement(len(frequency), Availability.AVAILABLE, ids, len(card))
        repeated = Measurement(
            len(card) - len(frequency), Availability.AVAILABLE, repeated_ids, len(card)
        )
    if card and all(country_values):
        counts = Counter(country_values)
        countries = Measurement(len(counts), Availability.AVAILABLE, ids, len(card))
        dominant = ratio(max(counts.values()), len(card), minimum, ids)
    return {
        Feature.DISTINCT_CARDS: distinct,
        Feature.REPEATED_CARD_ATTEMPTS: repeated,
        Feature.DISTINCT_CARD_COUNTRIES: countries,
        Feature.DOMINANT_COUNTRY_SHARE: dominant,
    }


def measurements(
    attempts: tuple[Attempt, ...], policy: StatisticalPolicy
) -> tuple[Measurement, ...]:
    captured = tuple(row for row in attempts if row.status == TransactionStatus.CAPTURED)
    card = tuple(row for row in attempts if row.payment_method != PaymentMethod.ACH)
    ids, captured_ids = attempt_ids(attempts), attempt_ids(captured)
    counts = Counter(row.status for row in attempts)
    base = sum(row.amount_cents for row in captured)
    tips = sum(row.tip_cents for row in captured)
    amounts = [Decimal(row.amount_cents) for row in captured]
    rules = {rule.feature: rule for rule in policy.rules}
    available = Availability.AVAILABLE
    with localcontext(ARITHMETIC):
        values = {
            Feature.PAYOUT_CENTS: Measurement(base + tips, available, captured_ids),
            Feature.CAPTURED_COUNT: Measurement(len(captured), available, captured_ids),
            Feature.ATTEMPT_COUNT: Measurement(len(attempts), available, ids),
            Feature.MEDIAN_BASE_CENTS: (
                Measurement(median(amounts), available, captured_ids)
                if amounts
                else Measurement(None, Availability.INAPPLICABLE)
            ),
            Feature.MAX_BASE_CENTS: (
                Measurement(max(amounts), available, captured_ids)
                if amounts
                else Measurement(None, Availability.INAPPLICABLE)
            ),
            Feature.DECLINE_RATE: ratio(
                counts[TransactionStatus.DECLINED],
                len(attempts),
                rules[Feature.DECLINE_RATE].minimum_denominator,
                ids,
            ),
            Feature.REFUND_RATE: ratio(
                counts[TransactionStatus.REFUNDED],
                len(attempts),
                rules[Feature.REFUND_RATE].minimum_denominator,
                ids,
            ),
            Feature.TIP_RATIO: ratio(
                tips, base, rules[Feature.TIP_RATIO].minimum_denominator, captured_ids
            ),
            **card_measurements(card, rules[Feature.DOMINANT_COUNTRY_SHARE].minimum_denominator),
        }
    for feature, method in (
        (Feature.CARD_ATTEMPTS, PaymentMethod.CARD),
        (Feature.TAP_TO_PAY_ATTEMPTS, PaymentMethod.TAP_TO_PAY),
        (Feature.ACH_ATTEMPTS, PaymentMethod.ACH),
    ):
        matching = tuple(row for row in attempts if row.payment_method == method)
        values[feature] = Measurement(
            len(matching), available, attempt_ids(matching), len(attempts)
        )
    peak = peak_window(attempts, policy.velocity_minutes)
    values[Feature.PEAK_ATTEMPTS] = Measurement(len(peak), available, attempt_ids(peak))
    return tuple(values[feature] for feature in Feature)


def extract_features(
    observation: Observation,
    policy: StatisticalPolicy,
    *,
    attempts: tuple[Attempt, ...] | None = None,
    complete: bool | None = None,
) -> FeatureObservation:
    payout = observation.payout
    if attempts is None:
        attempts = observation.attempts
    if complete is None:
        complete = (
            not isinstance(observation, HistoricalObservation) or observation.coverage_complete
        )
    weekend = sum(
        (payout.covers_from + timedelta(days=offset)).weekday() >= 5
        for offset in range(payout.coverage_days)
    )
    values = (
        measurements(attempts, policy)
        if complete
        else (Measurement(None, Availability.UNAVAILABLE, attempt_ids(attempts)),) * len(Feature)
    )
    return FeatureObservation(
        payout.payout_id,
        payout.team_id,
        observation.profession,
        payout.created_at,
        payout.covers_from,
        payout.covers_to,
        Coverage(payout.coverage_days, weekend),
        values,
    )
