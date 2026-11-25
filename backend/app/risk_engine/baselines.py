from collections import defaultdict
from decimal import Decimal, localcontext
from statistics import median

from app.risk_engine.feature_schema import ARITHMETIC, Feature, StatisticalPolicy
from app.risk_engine.features import FeatureObservation
from app.schemas.risk_findings import Baseline, BaselineScope, Reliability


def percentile(ordered: list[Decimal], fraction: Decimal) -> Decimal:
    """Linear interpolation at (n - 1) * fraction on ordered values."""
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def distribution(values: list[Decimal]) -> dict[str, Decimal]:
    ordered = sorted(values)
    with localcontext(ARITHMETIC):
        q1 = percentile(ordered, Decimal(".25"))
        q3 = percentile(ordered, Decimal(".75"))
        return {
            "q1": q1,
            "median": percentile(ordered, Decimal(".50")),
            "q3": q3,
            "iqr": q3 - q1,
        }


def build_baseline(
    subject: FeatureObservation,
    rows: tuple[FeatureObservation, ...],
    feature: Feature,
    scope: BaselineScope,
    policy: StatisticalPolicy,
) -> Baseline:
    comparable = [
        row
        for row in rows
        if row.payout_id != subject.payout_id
        and row.created_at < subject.created_at
        and row.coverage == subject.coverage
        and row.get(feature).value is not None
        and (
            row.team_id == subject.team_id
            if scope == BaselineScope.TEAM
            else row.team_id != subject.team_id and row.profession == subject.profession
        )
    ]
    prefilter_count = len(comparable)
    peers = None
    excluded_peers = 0
    with localcontext(ARITHMETIC):
        if scope == BaselineScope.TEAM:
            values = [Decimal(row.get(feature).value) for row in comparable]
            enough_peers = True
            minimum = policy.team_samples
        else:
            grouped: dict[str, list[FeatureObservation]] = defaultdict(list)
            for row in comparable:
                grouped[row.team_id].append(row)
            retained = [rows for rows in grouped.values() if len(rows) >= policy.samples_per_peer]
            peers = len(retained)
            excluded_peers = len(grouped) - peers
            comparable = [row for rows in retained for row in rows]
            values = [median(Decimal(row.get(feature).value) for row in rows) for rows in retained]
            enough_peers = peers >= policy.peer_teams
            minimum = policy.profession_samples
    if not prefilter_count:
        reliability = Reliability.NO_COMPARABLE_VALUES
    elif not enough_peers:
        reliability = Reliability.TOO_FEW_PEERS
    elif len(comparable) < minimum:
        reliability = Reliability.TOO_FEW_OBSERVATIONS
    else:
        reliability = Reliability.AVAILABLE

    return Baseline(
        scope=scope,
        reliability=reliability,
        coverage=subject.coverage,
        sample_count=len(comparable),
        peer_team_count=peers,
        excluded_peer_count=excluded_peers,
        prefilter_sample_count=prefilter_count,
        distribution_count=len(values),
        period_from=min((row.covers_from for row in comparable), default=None),
        period_to=max((row.covers_to for row in comparable), default=None),
        payout_ids=tuple(sorted(row.payout_id for row in comparable)),
        transaction_ids=tuple(
            sorted({txn_id for row in comparable for txn_id in row.get(feature).transaction_ids})
        ),
        **(distribution(values) if reliability == Reliability.AVAILABLE else {}),
    )
