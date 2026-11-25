import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.values import PaymentMethod, ReasonCode, TransactionStatus
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
POLICY_VERSION = "fraud-evidence-v2"
TEST_SEQUENCE_WINDOW = timedelta(hours=2)
CASE_CLOCK_TOLERANCE_MINUTES = 15
CASE_TIME_ZONE = ZoneInfo("Asia/Kolkata")


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
    witnesses: tuple[Attempt, ...] = ()

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
    witnesses = tuple(
        row for row in seed.witnesses if attempt is None or card_key(row) == card_key(attempt)
    )
    return refs(
        (EvidenceType.PAYOUT, seed.payout_id),
        (EvidenceType.TEAM, seed.team_id),
        *((EvidenceType.TRANSACTION, row.txn_id) for row in attempts),
        *((EvidenceType.TRANSACTION, row.txn_id) for row in witnesses),
        *((EvidenceType.TEAM, row.team_id) for row in witnesses),
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


def normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def matches_witness_clues(
    attempt: Attempt, anchor: Attempt, foreign: bool, clock_minutes: int | None
) -> bool:
    if foreign:
        country = (anchor.card_country or "").strip().upper()
        if (
            not re.fullmatch(r"[A-Z]{2}", country)
            or country == "US"
            or (attempt.card_country or "").strip().upper() != country
        ):
            return False
    if clock_minutes is None:
        return True
    local = attempt.created_at.astimezone(CASE_TIME_ZONE)
    distance = abs(local.hour * 60 + local.minute - clock_minutes)
    return min(distance, 24 * 60 - distance) <= CASE_CLOCK_TOLERANCE_MINUTES


def reconstruct_cards(
    row: HistoricalObservation,
    available: dict[str, Attempt],
    inputs: EvaluationInputs,
    teams: dict[str, TeamInput],
) -> tuple[set[str], tuple[Attempt, ...], str | None]:
    """Corroborate narrow, quantified descriptions against historical payment records."""
    raw_note = (row.disposition.reason or "").casefold()
    note = normalized_words(raw_note)
    ring = row.disposition.reason_code == ReasonCode.FRAUD_CARD_RING
    testing = row.disposition.reason_code == ReasonCode.FRAUD_CARD_TESTING
    count = re.search(r"\b([1-9][0-9]{0,2}) (foreign )?cards?\b", note)
    amounts = re.search(
        r"\$([0-9]{1,6})\s*[-–]\s*\$?([0-9]{1,6})\s+test charges\s+at\s+(.+?)\s+"
        r"then (?:(?:was|were) )?charged\s+\$([0-9]{1,6}(?:,[0-9]{3})?)\+",
        raw_note,
    )
    if not count or not (
        (ring and f"same {count[0]}" in note and "paid" in note and int(count[1]) >= 2)
        or (testing and amounts)
    ):
        return set(), (), None
    if re.search(r"\b(?:not|unclear|unconfirmed|unknown|possibly)\b", note):
        return set(), (), "ambiguous_case_description"
    minimum, maximum, large = (0, 0, 0)
    participants = note
    clock_minutes = None
    if testing and amounts:
        minimum, maximum, large = (
            int(amounts[group].replace(",", "")) * 100 for group in (1, 2, 4)
        )
        participants = normalized_words(amounts[3])
        clock = re.search(r"\baround\s+(1[0-2]|[1-9]):([0-5][0-9])\s*(am|pm)\s+ist\b", raw_note)
        if clock:
            clock_minutes = (int(clock[1]) % 12 + 12 * (clock[3] == "pm")) * 60 + int(clock[2])
        elif " around " in f" {participants} ":
            return set(), (), "unconfirmed_case_time"
        if not 0 < minimum <= maximum < large:
            return set(), (), "invalid_case_amounts"

    named: dict[str, list[str]] = {}
    for team in teams.values():
        name = normalized_words(team.team_name)
        if name and f" {name} " in f" {participants} ":
            named.setdefault(name, []).append(team.team_id)
    if any(len(matches) != 1 for matches in named.values()):
        return set(), (), "ambiguous_case_team"
    subject = row.payout.team_id
    named_ids = {matches[0] for matches in named.values()}
    peers = named_ids - {subject}
    if (
        subject not in teams
        or (ring and subject not in named_ids)
        or len(peers) < (2 if testing else 1)
    ):
        return set(), (), None
    if ring and "device" in note:
        device = profile_keys(teams[subject]).get(NodeKind.DEVICE)
        if (
            not device
            or f" {normalized_words(device)} " not in f" {note} "
            or any(profile_keys(teams[key]).get(NodeKind.DEVICE) != device for key in peers)
        ):
            return set(), (), "unconfirmed_case_device"

    by_card: dict[str, dict[str, dict[str, Attempt]]] = {}
    for history in inputs.history:
        team_id = history.payout.team_id
        if team_id not in peers:
            continue
        for item in visible_attempts(
            history, row.disposition.decided_at, inputs.snapshot_at
        ).values():
            if (key := card_key(item)) is not None:
                by_card.setdefault(key, {}).setdefault(team_id, {})[item.txn_id] = item
    if testing:
        for item in inputs.historical_fraud_attempts:
            if (
                item.team_id in peers
                and item.created_at <= row.disposition.decided_at
                and item.provenance.imported_at <= inputs.snapshot_at
                and (key := card_key(item)) is not None
            ):
                by_card.setdefault(key, {}).setdefault(item.team_id, {})[item.txn_id] = item

    matched: dict[str, dict[str, Attempt]] = {}
    anchors: set[str] = set()
    for item in available.values():
        key = card_key(item)
        if item.status != TransactionStatus.CAPTURED or key is None:
            continue
        evidence = {}
        for peer in peers:
            candidates = by_card.get(key, {}).get(peer, {}).values()
            related = [
                other
                for other in candidates
                if (
                    ring
                    and other.status == TransactionStatus.CAPTURED
                    and other.collection_date == item.collection_date
                )
                or (
                    testing
                    and timedelta() < item.created_at - other.created_at <= TEST_SEQUENCE_WINDOW
                    and minimum <= other.amount_cents <= maximum
                    and item.amount_cents >= large
                    and matches_witness_clues(other, item, bool(count[2]), clock_minutes)
                )
            ]
            if not related:
                break
            evidence.update((other.txn_id, other) for other in related)
        else:
            matched.setdefault(key, {}).update(evidence)
            anchors.add(item.txn_id)
    if len(matched) != int(count[1]):
        return set(), (), "unconfirmed_case_pattern"
    witnesses = {item.txn_id: item for group in matched.values() for item in group.values()}
    return anchors, tuple(witnesses[key] for key in sorted(witnesses)), None


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
        witnesses: tuple[Attempt, ...] = ()
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
            if not ids and issue is None:
                ids, witnesses, issue = reconstruct_cards(row, available, inputs, teams)
                if witnesses:
                    recognized_at = max(
                        recognized_at,
                        *(item.provenance.imported_at for item in witnesses),
                        *(teams[item.team_id].provenance.imported_at for item in witnesses),
                        teams[row.payout.team_id].provenance.imported_at,
                    )
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
                witnesses,
            )
        )
    return tuple(seeds), tuple(unresolved)
