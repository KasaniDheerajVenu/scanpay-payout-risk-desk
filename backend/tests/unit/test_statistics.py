from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.models.values import (
    BatchMode,
    HistoricalDecision,
    PaymentMethod,
    Profession,
    ReasonCode,
    SourceKind,
    TransactionStatus,
)
from app.risk_engine.baselines import build_baseline, distribution
from app.risk_engine.eligibility import monitoring_input
from app.risk_engine.feature_schema import Availability, Feature, StatisticalPolicy
from app.risk_engine.features import extract_features
from app.risk_engine.inputs import (
    Attempt,
    CollectionAvailability,
    Disposition,
    Eligibility,
    EvaluationConfig,
    EvaluationInputs,
    HistoricalObservation,
    PayoutInput,
    PendingObservation,
    Provenance,
    SelectedBatch,
)
from app.schemas.risk_findings import BaselineScope, ComparisonStatus, Reliability
from app.services.statistical_analysis_service import (
    FraudExclusion,
    HistoryUse,
    analyze_inputs,
    analyze_observation,
    evaluation_config,
)

OBSERVED = datetime(2025, 3, 3, 12, tzinfo=UTC)
FIRST_IMPORT = datetime(2025, 5, 1, tzinfo=UTC)
LATER_IMPORT = FIRST_IMPORT + timedelta(days=1)
PROVENANCE = Provenance(UUID(int=1), FIRST_IMPORT, FIRST_IMPORT)
POLICY = StatisticalPolicy(team_samples=2, profession_samples=4)
CAPTURE = TransactionStatus.CAPTURED
DECLINE = TransactionStatus.DECLINED
REFUND = TransactionStatus.REFUNDED


def attempt(
    txn_id,
    *,
    status=CAPTURE,
    cents=1000,
    tip=0,
    minutes=0,
    method=PaymentMethod.CARD,
    fingerprint="fixture-card-A",
    country="US",
    day=date(2025, 3, 2),
    team_id="team-a",
    provenance=PROVENANCE,
):
    created_at = datetime.combine(day, datetime.min.time(), UTC) + timedelta(minutes=minutes)
    return Attempt(
        txn_id,
        team_id,
        created_at,
        day,
        cents,
        tip,
        method,
        fingerprint,
        country,
        status,
        "private synthetic receipt name",
        "00000",
        provenance,
    )


def raw(
    payout_id,
    *,
    team="team-a",
    weeks=0,
    cents=10000,
    coverage=1,
    profession=Profession.PLUMBING,
    provenance=PROVENANCE,
    attempts=None,
):
    created_at = OBSERVED - timedelta(weeks=weeks)
    covered = created_at.date() - timedelta(days=3)
    if attempts is None:
        attempts = (
            attempt(
                f"{payout_id}-txn", cents=cents, day=covered, team_id=team, provenance=provenance
            ),
        )
    captures = [row for row in attempts if row.status == CAPTURE]
    return PendingObservation(
        PayoutInput(
            payout_id,
            team,
            created_at,
            created_at.date(),
            covered - timedelta(days=coverage - 1),
            covered,
            len(captures),
            sum(row.amount_cents + row.tip_cents for row in captures),
            SourceKind.PENDING,
            provenance,
        ),
        profession,
        attempts,
    )


def historical(
    source,
    *,
    reason=None,
    decision=HistoricalDecision.AUTO_APPROVED,
    decided=None,
    imported=None,
    complete=True,
):
    payout = replace(source.payout, source_kind=SourceKind.HISTORY)
    published = imported or payout.provenance.imported_at
    return HistoricalObservation(
        payout,
        source.profession,
        source.attempts,
        Disposition(
            decision,
            decided or payout.created_at + timedelta(hours=1),
            "fixture auditor",
            reason,
            "private synthetic historical note",
            UUID(int=1),
            published,
            published,
            1,
        ),
        complete,
        Eligibility.NORMAL_CANDIDATE if complete else Eligibility.INCOMPLETE_COVERAGE,
    )


def feature_row(payout_id, **kwargs):
    return extract_features(raw(payout_id, **kwargs), POLICY)


def by_feature(analysis, feature):
    return analysis.comparisons[list(Feature).index(feature)]


def snapshot(history, pending, *, extra_days=(), capture_at=LATER_IMPORT):
    days = {
        CollectionAvailability(row.team_id, row.collection_date, row.provenance.imported_at)
        for observed in history
        for row in observed.attempts
    }
    day = pending.payout.created_date
    cutoff = pending.payout.created_at + timedelta(days=1)
    batch = SelectedBatch(
        UUID(int=2),
        "local",
        BatchMode.INITIAL,
        cutoff,
        FIRST_IMPORT,
        "fixture-digest",
        day,
        (pending.payout.payout_id,),
    )
    return EvaluationInputs(
        batch,
        capture_at,
        EvaluationConfig(),
        (),
        (pending,),
        (),
        tuple(history),
        (),
        monitoring_input(day, ()),
        tuple(days) + tuple(extra_days),
    )


def test_features_use_captured_cents_and_all_attempts_with_exact_velocity_evidence():
    rows = (
        attempt("a", cents=1000, tip=100, minutes=0),
        attempt(
            "b",
            cents=3000,
            tip=200,
            minutes=4,
            method=PaymentMethod.TAP_TO_PAY,
            fingerprint="fixture-card-B",
            country="ca",
        ),
        attempt("c", status=DECLINE, minutes=9),
        attempt(
            "d",
            status=REFUND,
            minutes=11,
            method=PaymentMethod.TAP_TO_PAY,
            fingerprint="fixture-card-B",
            country="CA",
        ),
        attempt(
            "e",
            cents=2000,
            minutes=22,
            method=PaymentMethod.ACH,
            fingerprint="ignore-on-ach",
            country="ignore-on-ach",
        ),
    )
    source = raw("p", attempts=rows)
    metrics = extract_features(source, POLICY)
    expected = {
        Feature.PAYOUT_CENTS: 6300,
        Feature.CAPTURED_COUNT: 3,
        Feature.ATTEMPT_COUNT: 5,
        Feature.MEDIAN_BASE_CENTS: 2000,
        Feature.MAX_BASE_CENTS: 3000,
        Feature.DECLINE_RATE: Decimal(".2"),
        Feature.REFUND_RATE: Decimal(".2"),
        Feature.TIP_RATIO: Decimal(".05"),
        Feature.DISTINCT_CARDS: 2,
        Feature.REPEATED_CARD_ATTEMPTS: 2,
        Feature.CARD_ATTEMPTS: 2,
        Feature.TAP_TO_PAY_ATTEMPTS: 2,
        Feature.ACH_ATTEMPTS: 1,
        Feature.DISTINCT_CARD_COUNTRIES: 2,
        Feature.PEAK_ATTEMPTS: 3,
    }
    for feature, value in expected.items():
        assert metrics.get(feature).value == value
    assert metrics.get(Feature.PAYOUT_CENTS).transaction_ids == ("a", "b", "e")
    assert metrics.get(Feature.REPEATED_CARD_ATTEMPTS).transaction_ids == ("a", "b", "c", "d")
    assert metrics.get(Feature.PEAK_ATTEMPTS).transaction_ids == ("a", "b", "c")
    assert (
        metrics.get(Feature.DOMINANT_COUNTRY_SHARE).availability
        == Availability.INSUFFICIENT_OBSERVATIONS
    )
    assert extract_features(historical(source), POLICY).measurements == metrics.measurements
    assert extract_features(replace(source, attempts=rows[::-1]), POLICY) == metrics


def test_missing_zero_inapplicable_and_small_denominators_are_distinct():
    empty = feature_row("empty", attempts=())
    assert empty.get(Feature.PAYOUT_CENTS).value == 0
    assert empty.get(Feature.DECLINE_RATE).availability == Availability.INAPPLICABLE
    assert empty.get(Feature.MAX_BASE_CENTS).value is None
    no_cards = feature_row(
        "ach",
        attempts=(
            attempt(
                "zero", cents=0, tip=9, method=PaymentMethod.ACH, fingerprint=None, country=None
            ),
        ),
    )
    assert no_cards.get(Feature.PAYOUT_CENTS).value == 9
    assert no_cards.get(Feature.TIP_RATIO).availability == Availability.INAPPLICABLE
    assert no_cards.get(Feature.DISTINCT_CARDS).availability == Availability.INAPPLICABLE
    assert no_cards.get(Feature.DECLINE_RATE).availability == Availability.INSUFFICIENT_OBSERVATIONS
    partial = feature_row(
        "partial",
        attempts=(
            attempt("one", cents=999, fingerprint="  ", country=None),
            attempt("two", status=DECLINE),
        ),
    )
    assert partial.get(Feature.REPEATED_CARD_ATTEMPTS).availability == Availability.UNAVAILABLE
    assert partial.get(Feature.DISTINCT_CARD_COUNTRIES).availability == Availability.UNAVAILABLE
    assert partial.get(Feature.TIP_RATIO).availability == Availability.INSUFFICIENT_OBSERVATIONS
    two = feature_row("two", attempts=(attempt("one", cents=1000), attempt("two", cents=1001)))
    assert two.get(Feature.MEDIAN_BASE_CENTS).value == Decimal("1000.5")
    assert two.get(Feature.REPEATED_CARD_ATTEMPTS).value == 1
    assert (
        feature_row("distinct", attempts=(attempt("a"),)).get(Feature.REPEATED_CARD_ATTEMPTS).value
        == 0
    )


def test_quartiles_and_profession_weights_each_peer_equally_with_leave_team_out():
    assert distribution([Decimal(n) for n in (10000, 12000, 14000, 16000)]) == {
        "q1": 11500,
        "median": 13000,
        "q3": 14500,
        "iqr": 3000,
    }
    current = feature_row("current")
    light = tuple(feature_row(f"l{x}", team="light", weeks=x, cents=10000) for x in (1, 2))
    heavy = tuple(feature_row(f"h{x}", team="heavy", weeks=x, cents=30000) for x in range(1, 10))
    own = feature_row("own", weeks=1, cents=900000)
    outside = (
        feature_row("different", team="different", weeks=1, profession=Profession.HVAC),
        feature_row("weekend", team="light", weeks=1, coverage=3),
        feature_row("future", team="light", weeks=-1),
        feature_row("one-sample", team="thin", weeks=1),
    )
    baseline = build_baseline(
        current,
        (*light, *heavy, own, *outside, current),
        Feature.PAYOUT_CENTS,
        BaselineScope.PROFESSION,
        POLICY,
    )
    assert baseline.reliability == Reliability.AVAILABLE
    assert (baseline.sample_count, baseline.peer_team_count, baseline.distribution_count) == (
        11,
        2,
        2,
    )
    assert baseline.prefilter_sample_count == 12 and baseline.excluded_peer_count == 1
    assert (baseline.q1, baseline.median, baseline.q3, baseline.iqr) == (15000, 20000, 25000, 10000)
    assert baseline.payout_ids == tuple(sorted(row.payout_id for row in (*light, *heavy)))
    analysis = analyze_observation(current, (*light, *heavy, own), POLICY)
    comparison = by_feature(analysis, Feature.PAYOUT_CENTS)
    assert comparison.team.baseline.reliability == Reliability.TOO_FEW_OBSERVATIONS
    assert comparison.preferred_scope == BaselineScope.PROFESSION
    assert (
        by_feature(
            analyze_observation(current, heavy, POLICY), Feature.PAYOUT_CENTS
        ).profession.baseline.reliability
        == Reliability.TOO_FEW_PEERS
    )


@pytest.mark.parametrize(
    ("amount", "status", "direction"),
    [
        (13900, ComparisonStatus.IN_RANGE, None),
        (21000, ComparisonStatus.ANOMALY, "high"),
        (5000, ComparisonStatus.ANOMALY, "low"),
    ],
)
def test_actual_iqr_fences_directions_and_evidence(amount, status, direction):
    older = tuple(
        feature_row(f"older-{i}", weeks=i, cents=value)
        for i, value in enumerate((10000, 12000, 14000, 16000), 1)
    )
    result = analyze_observation(feature_row("current", cents=amount), older, POLICY)
    finding = by_feature(result, Feature.PAYOUT_CENTS).team
    assert (finding.status, finding.direction) == (status, direction)
    assert (finding.lower_fence, finding.upper_fence) == (7000, 19000)
    assert finding.current_transaction_ids == ("current-txn",)
    assert set(finding.baseline.transaction_ids) == {f"older-{i}-txn" for i in range(1, 5)}
    if status == ComparisonStatus.ANOMALY:
        assert f"${amount / 100:.2f}" in finding.explanation
        assert "the team's comparable history" in finding.explanation
        assert (
            len([item for item in result.findings if item.status == ComparisonStatus.ANOMALY]) >= 2
        )
    serialized = finding.model_dump_json()
    assert all(
        secret not in serialized
        for secret in ("receipt name", "fixture-card", "fraud", "NaN", "Infinity")
    )


def test_zero_spread_and_zero_median_require_absolute_materiality():
    def rates(key, weeks, declines):
        rows = tuple(
            attempt(f"{key}-{i}", status=DECLINE if i < declines else CAPTURE) for i in range(10)
        )
        return feature_row(key, weeks=weeks, attempts=rows)

    older = (rates("one", 1, 0), rates("two", 2, 0))
    small = by_feature(
        analyze_observation(rates("small", 0, 1), older, POLICY), Feature.DECLINE_RATE
    ).team
    large = by_feature(
        analyze_observation(rates("large", 0, 2), older, POLICY), Feature.DECLINE_RATE
    ).team
    assert small.status == ComparisonStatus.NONMATERIAL
    assert large.status == ComparisonStatus.ANOMALY
    assert large.baseline.iqr == large.upper_fence == large.baseline.median == 0
    assert large.relative_deviation is None and large.absolute_deviation == Decimal(".2")
    assert "zero median" in large.explanation
    money = (feature_row("f1", weeks=1), feature_row("f2", weeks=2))
    mild = by_feature(
        analyze_observation(feature_row("mild", cents=10500), money, POLICY), Feature.PAYOUT_CENTS
    )
    assert mild.team.status == ComparisonStatus.NONMATERIAL
    normal = by_feature(
        analyze_observation(feature_row("same"), money, POLICY), Feature.PAYOUT_CENTS
    )
    assert normal.team.relative_deviation == 0
    unknown = by_feature(
        analyze_observation(feature_row("unknown"), (), POLICY), Feature.PAYOUT_CENTS
    )
    assert unknown.preferred_scope is None
    assert len(unknown.findings) == 2


def test_temporal_training_vectors_exclude_future_availability_and_reserved_history():
    old = (historical(raw("old1", weeks=4)), historical(raw("old2", weeks=3, cents=20000)))
    subject = historical(raw("subject", weeks=1, cents=20000))
    action = raw("action", cents=999999)
    base = analyze_inputs(snapshot((*old, subject), action), POLICY)
    early = next(row for row in base.history if row.analysis.observation.payout_id == "subject")
    old_late_stamp = Provenance(UUID(int=9), LATER_IMPORT, FIRST_IMPORT - timedelta(days=1))
    future = (
        historical(raw("later-event", cents=30000)),
        historical(raw("old-but-arrived-late", weeks=2, cents=30000, provenance=old_late_stamp)),
        historical(raw("decision-made-late", weeks=2, cents=30000), decided=OBSERVED),
        historical(raw("disposition-arrived-late", weeks=2, cents=30000), imported=LATER_IMPORT),
        historical(raw("verified", weeks=2), reason=ReasonCode.OUTLIER_VERIFIED),
        historical(raw("victim", weeks=2), reason=ReasonCode.CARD_TESTING_VICTIM),
        historical(
            raw("confirmed", weeks=2),
            reason=ReasonCode.FRAUD_CARD_RING,
            decision=HistoricalDecision.REJECTED,
        ),
        historical(raw("missing", weeks=2, attempts=()), complete=False),
    )
    backfill = attempt(
        "late-old-decline",
        status=DECLINE,
        day=subject.payout.covers_from,
        provenance=old_late_stamp,
    )
    new_subject = replace(subject, attempts=(*subject.attempts, backfill))
    new_inputs = snapshot((*old, new_subject, *future), action)
    result = analyze_inputs(new_inputs, POLICY)
    historical_rows = {row.analysis.observation.payout_id: row for row in result.history}
    assert historical_rows["subject"].analysis.vector == early.analysis.vector
    assert by_feature(
        historical_rows["subject"].analysis, Feature.PAYOUT_CENTS
    ).team.baseline.payout_ids == ("old1", "old2")
    assert (
        historical_rows["verified"].use
        == historical_rows["victim"].use
        == HistoryUse.VALIDATION_ONLY
    )
    assert historical_rows["confirmed"].use == historical_rows["missing"].use == HistoryUse.EXCLUDED
    assert "action" not in {row.analysis.observation.payout_id for row in result.history}
    graph_flag = FraudExclusion(
        "old1", subject.payout.created_at + timedelta(hours=1), LATER_IMPORT
    )
    filtered = analyze_inputs(new_inputs, POLICY, exclusions=(graph_flag,))
    selected = next(
        row for row in filtered.history if row.analysis.observation.payout_id == "subject"
    )
    assert selected.analysis.vector == early.analysis.vector
    assert (
        "old1" not in by_feature(filtered.pending[0], Feature.PAYOUT_CENTS).team.baseline.payout_ids
    )
    assert filtered.input_digest != result.input_digest
    assert evaluation_config(POLICY) != evaluation_config(StatisticalPolicy())


def test_country_share_velocity_boundary_and_high_only_materiality():
    rows = tuple(
        attempt(
            f"tx-{i}",
            minutes=minute,
            country="US" if i < 4 else "CA",
            status=DECLINE if i < 2 else CAPTURE,
        )
        for i, minute in enumerate((0, 10, 21, 32, 43))
    )
    first = feature_row("one", weeks=1, attempts=rows)
    second = feature_row("two", weeks=2, attempts=rows)
    assert first.get(Feature.DOMINANT_COUNTRY_SHARE).value == Decimal(".8")
    assert first.get(Feature.PEAK_ATTEMPTS).transaction_ids == ("tx-0", "tx-1")
    calm = feature_row("calm", attempts=tuple(replace(row, status=CAPTURE) for row in rows))
    decline = by_feature(
        analyze_observation(calm, (first, second), POLICY), Feature.DECLINE_RATE
    ).team
    assert decline.status == ComparisonStatus.IN_RANGE
    assert decline.lower_fence is None and decline.upper_fence == Decimal(".4")

    high_baselines = (
        feature_row("old1", weeks=1, cents=1000000),
        feature_row("old2", weeks=2, cents=1000000),
    )
    small_relative = feature_row("current", cents=1006000)
    finding = by_feature(
        analyze_observation(small_relative, high_baselines, POLICY), Feature.PAYOUT_CENTS
    ).team
    assert finding.absolute_deviation == 6000 and finding.relative_deviation == Decimal(".006")
    assert finding.status == ComparisonStatus.NONMATERIAL
