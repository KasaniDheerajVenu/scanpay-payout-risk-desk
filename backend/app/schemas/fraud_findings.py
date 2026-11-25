from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.values import ReasonCode


class NodeKind(StrEnum):
    CASE = "historical_case"
    PAYOUT = "payout"
    TRANSACTION = "transaction"
    TEAM = "team"
    CARD = "card"
    PHONE = "owner_phone"
    DEVICE = "signup_device"
    BANK = "bank_routing_and_last4"


class EvidenceType(StrEnum):
    PAYOUT = "payout"
    TRANSACTION = "transaction"
    TEAM = "team"


class Strength(StrEnum):
    CONTEXT = "context"
    WEAK = "weak"
    UNRESOLVED = "unresolved"
    CORROBORATED = "corroborated"
    DIRECT = "direct"


class Relationship(StrEnum):
    PAYOUT_ATTEMPT = "payout_attempt"
    PAYOUT_TEAM = "payout_team"
    ATTEMPT_TEAM = "attempt_team"
    OBSERVED_CARD = "observed_card"
    PROFILE_IDENTIFIER = "profile_identifier"
    CASE_PAYOUT = "case_payout"
    CASE_TRANSACTION = "case_transaction"
    IMPLICATED_CARD = "implicated_card"
    IMPLICATED_PROFILE = "implicated_profile"
    DIRECT_CAPTURE = "direct_captured_implicated_card"
    CORROBORATED_PROFILE = "corroborated_implicated_phone_and_device"
    NONPAYABLE_CARD = "nonpayable_implicated_card_attempt"
    INCIDENTAL_CARD = "incidental_card_observation"
    WEAK_PROFILE = "weak_profile_match"
    UNRESOLVED_DESTINATION = "unresolved_destination_match"
    PRIOR_TEAM_CASE = "prior_team_case"
    UNRESOLVED_CASE = "unresolved_case"


class Exposure(StrEnum):
    ESTABLISHED = "established_exposure"
    UNRESOLVED = "unresolved_risk"
    WEAK = "weak_or_incidental"
    NONE = "no_supported_exposure"
    INSUFFICIENT = "insufficient_evidence"


class SafeModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class EvidenceRef(SafeModel):
    type: EvidenceType
    id: str


class PathNode(SafeModel):
    kind: NodeKind
    source_id: str | None = None


class EvidenceStep(SafeModel):
    start: PathNode
    end: PathNode
    relationship: Relationship
    strength: Strength
    supports_exposure: bool
    seed_payout_id: str | None = None
    known_at: datetime
    available_at: datetime
    evidence: tuple[EvidenceRef, ...]


class EvidencePath(SafeModel):
    seed_payout_id: str
    steps: tuple[EvidenceStep, ...]


class ClusterSnapshot(SafeModel):
    stable_case_key: str
    seed_payout_id: str
    reason_code: ReasonCode
    case_payout_ids: tuple[str, ...]
    reason_codes: tuple[ReasonCode, ...]
    summary: str
    evidence: tuple[EvidenceRef, ...]


class ClusterMatch(SafeModel):
    stable_case_key: str
    supporting_seed_ids: tuple[str, ...]
    relationship: Relationship
    strength: Strength
    current_evidence: tuple[EvidenceRef, ...]
    historical_evidence: tuple[EvidenceRef, ...]
    paths: tuple[EvidencePath, ...]


class InvestigativeLead(SafeModel):
    relationship: Relationship
    strength: Strength
    seed_payout_id: str
    identifier_categories: tuple[NodeKind, ...] = ()
    current_evidence: tuple[EvidenceRef, ...]
    historical_evidence: tuple[EvidenceRef, ...]


class UnresolvedSeed(SafeModel):
    seed_payout_id: str
    code: str
    evidence: tuple[EvidenceRef, ...]


class FraudFinding(SafeModel):
    payout_id: str
    status: Exposure
    established_exposure: bool
    current_evidence: tuple[EvidenceRef, ...]
    primary: ClusterMatch | None = None
    alternatives: tuple[ClusterMatch, ...] = ()
    leads: tuple[InvestigativeLead, ...] = ()
    coordinated_review_evidence: tuple[EvidenceRef, ...] = ()
    explanation: str
    policy_version: str
    mapping_version: str | None = None
