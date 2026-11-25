from dataclasses import replace
from decimal import Decimal

import pytest
from test_statistics import POLICY, attempt, feature_row, raw

from app.models.values import Decision, MLStatus
from app.risk_engine.feature_schema import FEATURE_VERSION, Direction, Feature, Unit
from app.risk_engine.features import extract_features
from app.risk_engine.hybrid_decisions import decide
from app.schemas.fraud_findings import (
    ClusterMatch,
    EvidenceRef,
    EvidenceType,
    Exposure,
    FraudFinding,
    InvestigativeLead,
    Relationship,
    Strength,
)
from app.schemas.ml_findings import MLFinding, UnusualFeature
from app.schemas.risk_findings import BaselineScope
from app.services.statistical_analysis_service import analyze_observation


def observations():
    cohort = tuple(feature_row(f"previous-{week}", weeks=week, cents=10000) for week in range(1, 7))
    result = analyze_observation(feature_row("pending"), cohort, POLICY)
    return result, cohort


def fraud_finding(status=Exposure.NONE):
    primary = {
        "stable_case_key": "known-case",
        "supporting_seed_ids": ("history",),
        "relationship": Relationship.DIRECT_CAPTURE,
        "strength": Strength.DIRECT,
        "current_evidence": (EvidenceRef(type=EvidenceType.TRANSACTION, id="pending-txn"),),
        "historical_evidence": (EvidenceRef(type=EvidenceType.PAYOUT, id="history"),),
        "paths": (),
    }
    leads = (
        (
            InvestigativeLead(
                relationship=Relationship.NONPAYABLE_CARD,
                strength=Strength.UNRESOLVED,
                seed_payout_id="history",
                current_evidence=primary["current_evidence"],
                historical_evidence=primary["historical_evidence"],
            ),
        )
        if status == Exposure.UNRESOLVED
        else ()
    )
    return FraudFinding(
        payout_id="pending",
        status=status,
        established_exposure=status == Exposure.ESTABLISHED,
        current_evidence=primary["current_evidence"],
        leads=leads,
        primary=ClusterMatch(**primary) if status == Exposure.ESTABLISHED else None,
        explanation="Review the linked historical case and captured payment.",
        policy_version="test-fraud",
    )


def ml_finding(status=MLStatus.INSUFFICIENT_HISTORY):
    evidence = (EvidenceRef(type=EvidenceType.TRANSACTION, id="pending-txn"),)
    return MLFinding(
        payout_id="pending",
        training_fingerprint="synthetic",
        feature_schema_version=FEATURE_VERSION,
        status=status,
        anomaly_score=0.8,
        anomaly_threshold=0.6,
        is_anomaly=True,
        observed_model_features=8,
        retained_model_features=8,
        feature_availability=(),
        qualifies_for_review=status == MLStatus.ACTIVE,
        evidence=evidence,
        unusual_features=(
            UnusualFeature(
                feature=Feature.PAYOUT_CENTS,
                family="amount",
                scope=BaselineScope.TEAM,
                unit=Unit.CENTS,
                observed=10000,
                historical_median=Decimal(8000),
                direction=Direction.HIGH,
                baseline_sample_count=5,
                evidence=evidence,
                comparison="The observed amount was $100 versus a comparable median of $80.",
            ),
        ),
        explanation="Fixture model context",
    )


@pytest.mark.parametrize(
    "status",
    [
        MLStatus.DIAGNOSTIC_ONLY,
        MLStatus.INSUFFICIENT_HISTORY,
        MLStatus.VALIDATION_FAILED,
        MLStatus.INFERENCE_FAILED,
    ],
)
def test_normal_payout_with_weak_link_approves_despite_ml_fallback(status):
    stats, _ = observations()
    result = decide(stats, fraud_finding(Exposure.WEAK), ml_finding(status))
    assert result.decision == Decision.APPROVE and result.cluster_key is None
    assert {item.id for item in result.evidence} >= {"pending", "team-a", "previous-1"}


def test_active_ml_only_and_meaningful_fraud_lead_trigger_review():
    stats, _ = observations()
    ml_only = decide(stats, fraud_finding(), ml_finding(MLStatus.ACTIVE))
    assert ml_only.decision == Decision.MANUAL_REVIEW and "validated ML alert" in ml_only.reason
    unresolved = decide(stats, fraud_finding(Exposure.UNRESOLVED), ml_finding())
    assert unresolved.decision == Decision.MANUAL_REVIEW
    assert "history" in {item.id for item in unresolved.evidence}
    sparse = replace(stats, comparisons=())
    uncertain = decide(sparse, fraud_finding(Exposure.INSUFFICIENT), ml_finding())
    assert uncertain.decision == Decision.MANUAL_REVIEW and "insufficient" in uncertain.reason


def test_tracks_statistical_findings_even_when_fraud_takes_precedence():
    _, cohort = observations()
    day = raw("pending").payout.covers_from
    large = analyze_observation(
        extract_features(
            raw("pending", attempts=(attempt("pending-txn", cents=30000, day=day),)), POLICY
        ),
        cohort,
        POLICY,
    )
    statistical = decide(large, fraud_finding(), ml_finding())
    assert statistical.decision == Decision.MANUAL_REVIEW
    assert "$300.00" in statistical.reason and "$100.00" in statistical.reason
    rejected = decide(large, fraud_finding(Exposure.ESTABLISHED), ml_finding(MLStatus.ACTIVE))
    assert rejected.decision == Decision.REJECT and rejected.cluster_key == "known-case"
    assert rejected.statistics == large and rejected.ml.qualifies_for_review
