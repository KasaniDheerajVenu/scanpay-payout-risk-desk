from dataclasses import dataclass

from app.models.values import Decision, MLStatus
from app.risk_engine.feature_schema import DEFINITIONS, Feature
from app.risk_engine.statistical_anomalies import format_value
from app.schemas.fraud_findings import EvidenceRef, EvidenceType, Exposure, FraudFinding, Strength
from app.schemas.ml_findings import MLFinding
from app.schemas.risk_findings import ComparisonStatus, StatisticalFinding
from app.services.statistical_analysis_service import ObservationAnalysis

RULE_VERSION = "hybrid-v1"
REQUIRED_NORMAL_CONTEXT = (Feature.PAYOUT_CENTS, Feature.CAPTURED_COUNT)


@dataclass(frozen=True, slots=True)
class DecisionDraft:
    payout_id: str
    team_id: str
    decision: Decision
    cluster_key: str | None
    reason: str
    evidence: tuple[EvidenceRef, ...]
    statistics: ObservationAnalysis
    fraud: FraudFinding
    ml: MLFinding


def statistical_evidence(item: StatisticalFinding) -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef(type=EvidenceType.PAYOUT, id=item.current_payout_id),
        *(EvidenceRef(type=EvidenceType.PAYOUT, id=key) for key in item.baseline.payout_ids),
        *(
            EvidenceRef(type=EvidenceType.TRANSACTION, id=key)
            for key in (*item.current_transaction_ids, *item.baseline.transaction_ids)
        ),
    )


def decide(stats: ObservationAnalysis, fraud: FraudFinding, ml: MLFinding) -> DecisionDraft:
    payout = stats.observation
    primary = fraud.primary
    cluster_key = None
    context: tuple[EvidenceRef, ...]
    if fraud.status == Exposure.ESTABLISHED and primary is not None:
        decision = Decision.REJECT
        cluster_key = primary.stable_case_key
        reason = fraud.explanation
        context = (*primary.current_evidence, *primary.historical_evidence)
    else:
        anomaly = next(
            (item for item in stats.findings if item.status == ComparisonStatus.ANOMALY), None
        )
        ml_review = (
            ml.status == MLStatus.ACTIVE
            and ml.qualifies_for_review
            and ml.is_anomaly is True
            and ml.anomaly_score is not None
            and ml.anomaly_threshold is not None
            and ml.anomaly_score > ml.anomaly_threshold
        )
        normal = {
            pair.team.feature: next(
                (
                    item
                    for item in (pair.team, pair.profession)
                    if item.status in {ComparisonStatus.IN_RANGE, ComparisonStatus.NONMATERIAL}
                ),
                None,
            )
            for pair in stats.comparisons
        }
        required = [feature for feature in REQUIRED_NORMAL_CONTEXT if normal.get(feature) is None]
        decision = Decision.MANUAL_REVIEW
        if fraud.status == Exposure.UNRESOLVED:
            reason = "Investigate the cited payment attempts and unresolved historical fraud links."
            context = tuple(
                ref
                for lead in fraud.leads
                if lead.strength == Strength.UNRESOLVED
                for ref in (*lead.current_evidence, *lead.historical_evidence)
            )
        elif anomaly is not None:
            label = DEFINITIONS[anomaly.feature].label
            observed = format_value(anomaly.observed, anomaly.unit)
            median = format_value(anomaly.baseline.median, anomaly.unit)
            scope = anomaly.baseline.scope.value.lower()
            reason = (
                f"{label} was {observed} versus a comparable {scope} median of {median}. "
                "Review the cited payments for a legitimate explanation."
            )
            context = statistical_evidence(anomaly)
        elif ml_review:
            comparison = ml.unusual_features[0].comparison
            reason = (
                f"{comparison} Compare the cited payment patterns behind the validated ML alert."
            )
            context = ml.evidence
        elif required:
            labels = " and ".join(DEFINITIONS[feature].label.lower() for feature in required)
            reason = (
                f"Comparable history is insufficient to assess {labels}. "
                "Verify the cited payments and recent business activity."
            )
            context = fraud.current_evidence
        else:
            decision = Decision.APPROVE
            reason = (
                "The payout amount and captured count are consistent with comparable history, "
                "and the available evidence shows no material reason to hold the payout."
            )
            context = tuple(
                ref
                for feature in REQUIRED_NORMAL_CONTEXT
                if (item := normal[feature]) is not None
                for ref in statistical_evidence(item)
            )
    evidence = tuple(
        dict.fromkeys(
            (
                EvidenceRef(type=EvidenceType.PAYOUT, id=payout.payout_id),
                EvidenceRef(type=EvidenceType.TEAM, id=payout.team_id),
                *context,
            )
        )
    )
    return DecisionDraft(
        payout.payout_id, payout.team_id, decision, cluster_key, reason, evidence, stats, fraud, ml
    )
