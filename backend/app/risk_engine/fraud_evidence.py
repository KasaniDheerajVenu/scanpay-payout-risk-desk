import re
from dataclasses import dataclass
from datetime import datetime

from app.models.values import PaymentMethod, ReasonCode
from app.risk_engine.eligibility import is_fraud_seed
from app.risk_engine.inputs import (
    Attempt,
    EvaluationInputs,
    HistoricalObservation,
    PendingObservation,
    TeamInput,
)
from app.schemas.fraud_findings import EvidenceRef, EvidenceType, NodeKind, UnresolvedSeed

PROFILE_KINDS = frozenset({NodeKind.DEVICE, NodeKind.PHONE, NodeKind.BANK})
DIRECTIVES = {
    "transaction": NodeKind.TRANSACTION,
    "card": NodeKind.CARD,
    "team": NodeKind.TEAM,
    "payout": NodeKind.PAYOUT,
    "owner phone": NodeKind.PHONE,
    "signup device": NodeKind.DEVICE,
    "bank routing and last4": NodeKind.BANK,
}
POLICY_VERSION = "fraud-evidence-v1"


@dataclass(frozen=True, slots=True, repr=False)
class MappedCase:
    seed_payout_id: str
    transaction_ids: tuple[str, ...]
    available_at: datetime
    implicated_profile_kinds: tuple[NodeKind, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class CaseMapping:
    version: str
    cases: tuple[MappedCase, ...]


@dataclass(frozen=True, slots=True, repr=False)
class Seed:
    history: HistoricalObservation
    attempts: tuple[Attempt, ...]
    implicated_profiles: frozenset[NodeKind]
    available_at: datetime

    @property
    def payout_id(self) -> str:
        return self.history.payout.payout_id

    @property
    def team_id(self) -> str:
        return self.history.payout.team_id

    @property
    def reason(self) -> ReasonCode:
        reason = self.history.disposition.reason_code
        assert reason is not None
        return reason

    @property
    def known_at(self) -> datetime:
        return self.history.disposition.decided_at


def refs(*entries: tuple[EvidenceType, str]) -> tuple[EvidenceRef, ...]:
    return tuple(EvidenceRef(type=kind, id=key) for kind, key in sorted(set(entries)))


def case_refs(seed: Seed, attempt: Attempt | None = None) -> tuple[EvidenceRef, ...]:
    attempts = seed.attempts if attempt is None else (attempt,)
    return refs(
        (EvidenceType.PAYOUT, seed.payout_id),
        (EvidenceType.TEAM, seed.team_id),
        *((EvidenceType.TRANSACTION, row.txn_id) for row in attempts),
    )


def card_key(attempt: Attempt) -> str | None:
    if attempt.payment_method == PaymentMethod.ACH:
        return None
    return (attempt.card_fingerprint or "").strip() or None


def normalize_profile(kind: NodeKind, value: str) -> str | None:
    value = value.strip()
    if kind == NodeKind.PHONE:
        if not re.fullmatch(r"\+?[0-9 ().-]+", value):
            return None
        value = re.sub(r"[^0-9]", "", value)
        return value if 7 <= len(value) <= 15 else None
    if kind == NodeKind.BANK:
        return value if re.fullmatch(r"[0-9]{9}:[0-9]{4}", value) else None
    return value or None


def profile_keys(team: TeamInput | None) -> dict[NodeKind, str]:
    if team is None:
        return {}
    source = {
        NodeKind.PHONE: team.owner_phone or "",
        NodeKind.DEVICE: team.signup_device_id or "",
        NodeKind.BANK: f"{team.payout_bank_routing}:{team.payout_bank_last4}",
    }
    return {kind: key for kind, value in source.items() if (key := normalize_profile(kind, value))}


def visible_attempts(
    observation: HistoricalObservation | PendingObservation, cutoff: datetime, snapshot: datetime
) -> dict[str, Attempt]:
    payout = observation.payout
    return {
        attempt.txn_id: attempt
        for attempt in observation.attempts
        if attempt.team_id == payout.team_id
        and payout.covers_from <= attempt.collection_date <= payout.covers_to
        and attempt.created_at <= min(cutoff, payout.created_at)
        and attempt.provenance.imported_at <= snapshot
    }


def note_evidence(
    row: HistoricalObservation, available: dict[str, Attempt], profiles: dict[NodeKind, str]
) -> tuple[set[str], set[NodeKind], str | None]:
    transaction_ids: set[str] = set()
    implicated: set[NodeKind] = set()
    for line in (row.disposition.reason or "").splitlines():
        prefix, sep, value = line.strip().partition(":")
        directive = prefix.casefold().removeprefix("implicated ")
        if not sep or not prefix.casefold().startswith("implicated "):
            continue
        kind = DIRECTIVES.get(directive)
        value = value.strip()
        if kind == NodeKind.TRANSACTION and value in available:
            transaction_ids.add(value)
        elif kind == NodeKind.CARD and value:
            matched = {key for key, attempt in available.items() if card_key(attempt) == value}
            if not matched:
                return set(), set(), "invalid_case_reference"
            transaction_ids.update(matched)
        elif (kind == NodeKind.PAYOUT and value == row.payout.payout_id) or (
            kind == NodeKind.TEAM and value == row.payout.team_id
        ):
            continue
        elif kind in profiles and normalize_profile(kind, value) == profiles[kind]:
            implicated.add(kind)
        else:
            return set(), set(), "invalid_case_reference"
    return transaction_ids, implicated, None


def mapping_index(mapping: CaseMapping | None) -> dict[str, MappedCase]:
    if mapping is None:
        return {}
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", mapping.version) or not mapping.cases:
        raise ValueError("invalid_fraud_case_mapping")
    index = {}
    for case in mapping.cases:
        if (
            not case.seed_payout_id
            or case.seed_payout_id in index
            or not case.transaction_ids
            or len(set(case.transaction_ids)) != len(case.transaction_ids)
            or case.available_at.utcoffset() is None
            or not set(case.implicated_profile_kinds) <= PROFILE_KINDS
            or len(set(case.implicated_profile_kinds)) != len(case.implicated_profile_kinds)
        ):
            raise ValueError("invalid_fraud_case_mapping")
        index[case.seed_payout_id] = case
    return index


def resolve_seeds(
    inputs: EvaluationInputs, mapping: CaseMapping | None = None
) -> tuple[tuple[Seed, ...], tuple[UnresolvedSeed, ...]]:
    selected = {
        row.payout.payout_id: row
        for row in inputs.history
        if is_fraud_seed(row.disposition)
        and row.payout.created_at <= row.disposition.decided_at <= inputs.batch.cutoff
        and row.disposition.available_at <= inputs.snapshot_at
        and row.payout.provenance.imported_at <= inputs.snapshot_at
    }
    mapped = mapping_index(mapping)
    if not mapped.keys() <= selected.keys():
        raise ValueError("invalid_fraud_case_mapping")
    teams = {
        team.team_id: team
        for team in inputs.teams
        if team.provenance.imported_at <= inputs.snapshot_at
    }
    seeds, unresolved = [], []
    for key, row in sorted(selected.items()):
        available = visible_attempts(row, inputs.batch.cutoff, inputs.snapshot_at)
        profiles = profile_keys(teams.get(row.payout.team_id))
        recognized_at = max(row.disposition.available_at, row.payout.provenance.imported_at)
        mapped_case = mapped.get(key)
        if mapped_case is not None:
            ids = set(mapped_case.transaction_ids)
            profile_kinds = set(mapped_case.implicated_profile_kinds)
            team = teams.get(row.payout.team_id)
            if (
                not ids <= available.keys()
                or not profile_kinds <= profiles.keys()
                or not recognized_at <= mapped_case.available_at <= inputs.snapshot_at
                or any(
                    available[item].provenance.imported_at > mapped_case.available_at
                    for item in ids
                )
                or (profile_kinds and team.provenance.imported_at > mapped_case.available_at)
            ):
                raise ValueError("invalid_fraud_case_mapping")
            issue = None
            recognized_at = mapped_case.available_at
        else:
            ids, profile_kinds, issue = note_evidence(row, available, profiles)
        if not ids:
            unresolved.append(
                UnresolvedSeed(
                    seed_payout_id=key,
                    code=issue or "case_evidence_required",
                    evidence=refs(
                        (EvidenceType.PAYOUT, key), (EvidenceType.TEAM, row.payout.team_id)
                    ),
                )
            )
            continue
        seeds.append(
            Seed(
                row,
                tuple(available[item] for item in sorted(ids)),
                frozenset(profile_kinds),
                recognized_at,
            )
        )
    return tuple(seeds), tuple(unresolved)
