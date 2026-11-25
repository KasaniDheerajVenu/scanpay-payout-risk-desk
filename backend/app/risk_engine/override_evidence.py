from typing import Any

from app.models.values import Decision
from app.schemas.decisions import DecisionProblem
from app.schemas.fraud_findings import EvidenceRef, EvidenceType, Exposure, FraudFinding, NodeKind


def references(kind: EvidenceType, ids: list[str]) -> set[EvidenceRef]:
    return {EvidenceRef(type=kind, id=key) for key in ids}


def published_evidence(
    payout_id: str,
    team_id: str,
    manifest: dict[str, Any],
    findings: dict[str, Any],
    original_evidence: list[dict[str, str]],
    fraud: FraudFinding | None,
) -> tuple[set[EvidenceRef], set[EvidenceRef]]:
    subject = references(EvidenceType.PAYOUT, [payout_id]) | references(
        EvidenceType.TEAM, [team_id]
    )
    allowed = subject | {EvidenceRef.model_validate(ref) for ref in original_evidence}
    for item in manifest.get("selected_pending", ()):
        if item["payout_id"] == payout_id:
            allowed.update(references(EvidenceType.TRANSACTION, item["attempt_ids"]))
    for pair in findings.get("statistics", ()):
        for scope in ("team", "profession"):
            baseline = pair[scope]["baseline"]
            allowed.update(references(EvidenceType.PAYOUT, baseline["payout_ids"]))
            allowed.update(references(EvidenceType.TRANSACTION, baseline["transaction_ids"]))
    allowed.update(
        EvidenceRef.model_validate(ref) for ref in findings.get("ml", {}).get("evidence", ())
    )
    if fraud is not None:
        allowed.update(fraud.current_evidence)
        for item in (*fraud.leads, fraud.primary, *fraud.alternatives):
            if item is not None:
                allowed.update(item.current_evidence)
                allowed.update(item.historical_evidence)
    return allowed, subject


def establishes_exposure(
    fraud: FraudFinding | None, stable_case_key: str, selected: set[EvidenceRef]
) -> bool:
    if fraud is None or fraud.status != Exposure.ESTABLISHED or not fraud.established_exposure:
        return False
    for match in (fraud.primary, *fraud.alternatives):
        if match is None or match.stable_case_key != stable_case_key:
            continue
        # Phase 9 already validated the linked paths and redacted raw matching identifiers.
        for path in match.paths:
            if not path.steps:
                continue
            captured = path.steps[0].end
            historical = path.steps[-1].start
            if (
                captured.kind == historical.kind == NodeKind.TRANSACTION
                and captured.source_id is not None
                and historical.source_id is not None
                and {
                    EvidenceRef(type=EvidenceType.TRANSACTION, id=captured.source_id),
                    EvidenceRef(type=EvidenceType.TRANSACTION, id=historical.source_id),
                    EvidenceRef(type=EvidenceType.PAYOUT, id=path.seed_payout_id),
                }
                <= selected
            ):
                return True
    return False


def validate_evidence(
    decision: Decision,
    evidence: tuple[EvidenceRef, ...],
    payout_id: str,
    team_id: str,
    manifest: dict[str, Any],
    findings: dict[str, Any],
    original_evidence: list[dict[str, str]],
    stable_case_key: str | None,
) -> None:
    raw_fraud = findings.get("fraud")
    fraud = FraudFinding.model_validate(raw_fraud) if raw_fraud else None
    allowed, subject = published_evidence(
        payout_id, team_id, manifest, findings, original_evidence, fraud
    )
    selected = set(evidence)
    if len(selected) != len(evidence) or not selected <= allowed:
        raise DecisionProblem("invalid_override_evidence")
    if decision == Decision.MANUAL_REVIEW and not any(
        ref.type in {EvidenceType.TRANSACTION, EvidenceType.PAYOUT} for ref in selected - subject
    ):
        raise DecisionProblem("review_evidence_required")
    if decision == Decision.REJECT and (
        stable_case_key is None or not establishes_exposure(fraud, stable_case_key, selected)
    ):
        raise DecisionProblem("override_exposure_not_established")
