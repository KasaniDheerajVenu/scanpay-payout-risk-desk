from collections import defaultdict
from dataclasses import dataclass

import networkx as nx

from app.models.values import PaymentMethod, ReasonCode, TransactionStatus
from app.risk_engine.fraud_evidence import POLICY_VERSION, Seed, card_key, case_refs, refs
from app.risk_engine.fraud_graph import Node, evidence_path, pending_attempts
from app.risk_engine.inputs import Attempt, EvaluationInputs, PendingObservation
from app.schemas.fraud_findings import (
    ClusterMatch,
    ClusterSnapshot,
    EvidencePath,
    EvidenceRef,
    EvidenceType,
    Exposure,
    FraudFinding,
    InvestigativeLead,
    NodeKind,
    Relationship,
    Strength,
    UnresolvedSeed,
)

CORROBORATION = frozenset({NodeKind.PHONE, NodeKind.DEVICE})
STRONGEST_FIRST = {Strength.DIRECT: 0, Strength.CORROBORATED: 1}


@dataclass(frozen=True, slots=True)
class SupportedPath:
    relationship: Relationship
    strength: Strength
    current: tuple[EvidenceRef, ...]
    historical: tuple[EvidenceRef, ...]
    path: EvidencePath


def pending_refs(row: PendingObservation, *attempts: Attempt) -> tuple[EvidenceRef, ...]:
    return refs(
        (EvidenceType.PAYOUT, row.payout.payout_id),
        (EvidenceType.TEAM, row.payout.team_id),
        *((EvidenceType.TRANSACTION, attempt.txn_id) for attempt in attempts),
    )


def matched_profiles(graph: nx.MultiGraph, current_team: str, seed_team: str) -> tuple[Node, ...]:
    current, historical = Node(NodeKind.TEAM, current_team), Node(NodeKind.TEAM, seed_team)
    if current == historical or current not in graph or historical not in graph:
        return ()
    return tuple(
        sorted(
            (
                node
                for node in graph.neighbors(current)
                if graph.has_edge(current, node, Relationship.PROFILE_IDENTIFIER)
                and graph.has_edge(historical, node, Relationship.PROFILE_IDENTIFIER)
            ),
            key=lambda node: node.kind,
        )
    )


def card_links(
    graph: nx.MultiGraph, row: PendingObservation, attempts: tuple[Attempt, ...], seed: Seed
) -> tuple[list[SupportedPath], list[InvestigativeLead]]:
    case = Node(NodeKind.CASE, seed.payout_id)
    matches, leads = [], []
    unsuccessful, implicated_unsuccessful = [], []
    for current in attempts:
        card = card_key(current)
        if card is None or not graph.has_edge(
            case, Node(NodeKind.CARD, card), Relationship.IMPLICATED_CARD
        ):
            continue
        for historic in seed.attempts:
            if card_key(historic) != card or historic.created_at > current.created_at:
                continue
            if current.status != TransactionStatus.CAPTURED:
                unsuccessful.append(current)
                implicated_unsuccessful.append(historic)
                continue
            current_tx = Node(NodeKind.TRANSACTION, current.txn_id)
            historical_tx = Node(NodeKind.TRANSACTION, historic.txn_id)
            card_node = Node(NodeKind.CARD, card)
            match = evidence_path(
                graph,
                seed,
                (
                    (
                        Node(NodeKind.PAYOUT, row.payout.payout_id),
                        Relationship.PAYOUT_ATTEMPT,
                        current_tx,
                    ),
                    (current_tx, Relationship.OBSERVED_CARD, card_node),
                    (card_node, Relationship.IMPLICATED_CARD, case),
                    (case, Relationship.CASE_TRANSACTION, historical_tx),
                    (
                        historical_tx,
                        Relationship.PAYOUT_ATTEMPT,
                        Node(NodeKind.PAYOUT, seed.payout_id),
                    ),
                ),
            )
            matches.append(
                SupportedPath(
                    Relationship.DIRECT_CAPTURE,
                    Strength.DIRECT,
                    pending_refs(row, current),
                    case_refs(seed, historic),
                    match,
                )
            )
    if unsuccessful:
        historical = refs(
            *((item.type, item.id) for item in case_refs(seed, implicated_unsuccessful[0])),
            *((EvidenceType.TRANSACTION, attempt.txn_id) for attempt in implicated_unsuccessful),
        )
        leads.append(
            InvestigativeLead(
                relationship=Relationship.NONPAYABLE_CARD,
                strength=Strength.UNRESOLVED,
                seed_payout_id=seed.payout_id,
                identifier_categories=(NodeKind.CARD,),
                current_evidence=pending_refs(row, *unsuccessful),
                historical_evidence=historical,
            )
        )
    return matches, leads


def profile_links(
    graph: nx.MultiGraph,
    row: PendingObservation,
    attempts: tuple[Attempt, ...],
    seed: Seed,
    victim: bool,
) -> tuple[list[SupportedPath], list[InvestigativeLead]]:
    common = matched_profiles(graph, row.payout.team_id, seed.team_id)
    if not common:
        return [], []
    case = Node(NodeKind.CASE, seed.payout_id)
    certified = {
        node.kind: node
        for node in common
        if graph.has_edge(case, node, Relationship.IMPLICATED_PROFILE)
    }
    current = next(
        (
            item
            for item in attempts
            if item.status == TransactionStatus.CAPTURED and item.created_at >= seed.known_at
        ),
        None,
    )
    history = next(
        (item for item in seed.attempts if item.status == TransactionStatus.CAPTURED), None
    )
    if (
        current is not None
        and history is not None
        and not victim
        and seed.reason != ReasonCode.FRAUD_ACCOUNT_TAKEOVER
        and CORROBORATION <= certified.keys()
    ):
        transaction = Node(NodeKind.TRANSACTION, current.txn_id)
        team = Node(NodeKind.TEAM, row.payout.team_id)
        historical_tx = Node(NodeKind.TRANSACTION, history.txn_id)
        matches = []
        for kind in sorted(CORROBORATION):
            path = evidence_path(
                graph,
                seed,
                (
                    (
                        Node(NodeKind.PAYOUT, row.payout.payout_id),
                        Relationship.PAYOUT_ATTEMPT,
                        transaction,
                    ),
                    (transaction, Relationship.ATTEMPT_TEAM, team),
                    (team, Relationship.PROFILE_IDENTIFIER, certified[kind]),
                    (certified[kind], Relationship.IMPLICATED_PROFILE, case),
                    (case, Relationship.CASE_TRANSACTION, historical_tx),
                    (
                        historical_tx,
                        Relationship.PAYOUT_ATTEMPT,
                        Node(NodeKind.PAYOUT, seed.payout_id),
                    ),
                ),
            )
            matches.append(
                SupportedPath(
                    Relationship.CORROBORATED_PROFILE,
                    Strength.CORROBORATED,
                    pending_refs(row, current),
                    case_refs(seed, history),
                    path,
                )
            )
        return matches, []
    return [], [
        InvestigativeLead(
            relationship=Relationship.WEAK_PROFILE,
            strength=Strength.WEAK,
            seed_payout_id=seed.payout_id,
            identifier_categories=tuple(node.kind for node in common),
            current_evidence=pending_refs(row),
            historical_evidence=case_refs(seed),
        )
    ]


def incidental_cards(
    graph: nx.MultiGraph, row: PendingObservation, attempts: tuple[Attempt, ...], seed: Seed
) -> InvestigativeLead | None:
    history_node = Node(NodeKind.PAYOUT, seed.payout_id)
    case = Node(NodeKind.CASE, seed.payout_id)
    supporting = set()
    current = []
    for attempt in attempts:
        if (card := card_key(attempt)) is None:
            continue
        node = Node(NodeKind.CARD, card)
        if graph.has_edge(case, node, Relationship.IMPLICATED_CARD):
            continue
        related = {
            other.key
            for other in graph.neighbors(node)
            if graph.has_edge(history_node, other, Relationship.PAYOUT_ATTEMPT)
        }
        if related:
            supporting.update(related)
            current.append(attempt)
    if not supporting:
        return None
    return InvestigativeLead(
        relationship=Relationship.INCIDENTAL_CARD,
        strength=Strength.WEAK,
        seed_payout_id=seed.payout_id,
        identifier_categories=(NodeKind.CARD,),
        current_evidence=pending_refs(row, *current),
        historical_evidence=refs(
            (EvidenceType.PAYOUT, seed.payout_id),
            (EvidenceType.TEAM, seed.team_id),
            *((EvidenceType.TRANSACTION, txn_id) for txn_id in supporting),
        ),
    )


def cluster_matches(
    paths: list[SupportedPath], cluster_by_seed: dict[str, ClusterSnapshot]
) -> tuple[ClusterMatch, ...]:
    groups = defaultdict(list)
    for item in paths:
        groups[cluster_by_seed[item.path.seed_payout_id].stable_case_key].append(item)
    matches = []
    for key, paths_for_cluster in groups.items():
        ordered = sorted(
            paths_for_cluster,
            key=lambda item: (
                STRONGEST_FIRST[item.strength],
                item.path.seed_payout_id,
                item.path.model_dump_json(),
            ),
        )
        matches.append(
            ClusterMatch(
                stable_case_key=key,
                supporting_seed_ids=tuple(sorted({item.path.seed_payout_id for item in ordered})),
                strength=ordered[0].strength,
                relationship=ordered[0].relationship,
                current_evidence=refs(
                    *((ref.type, ref.id) for item in ordered for ref in item.current)
                ),
                historical_evidence=refs(
                    *((ref.type, ref.id) for item in ordered for ref in item.historical)
                ),
                paths=tuple(item.path for item in ordered),
            )
        )
    return tuple(
        sorted(matches, key=lambda item: (STRONGEST_FIRST[item.strength], item.stable_case_key))
    )


def assess_pending(
    inputs: EvaluationInputs,
    graph: nx.MultiGraph,
    row: PendingObservation,
    seeds: tuple[Seed, ...],
    cluster_by_seed: dict[str, ClusterSnapshot],
    unresolved: tuple[UnresolvedSeed, ...],
    mapping_version: str | None,
) -> FraudFinding:
    attempts = tuple(sorted(pending_attempts(inputs, row).values(), key=lambda item: item.txn_id))
    victim = any(
        prior.payout.team_id == row.payout.team_id
        and prior.disposition.reason_code == ReasonCode.CARD_TESTING_VICTIM
        and prior.disposition.decided_at <= inputs.batch.cutoff
        and prior.disposition.available_at <= inputs.snapshot_at
        for prior in inputs.history
    )
    all_paths, leads = [], []
    for seed in seeds:
        paths, notes = card_links(graph, row, attempts, seed)
        indirect, weak = profile_links(graph, row, attempts, seed, victim)
        all_paths.extend(paths + indirect)
        leads.extend(notes + weak)
        incidental = incidental_cards(graph, row, attempts, seed)
        if incidental is not None:
            leads.append(incidental)
        if row.payout.team_id == seed.team_id and not paths:
            leads.append(
                InvestigativeLead(
                    relationship=Relationship.PRIOR_TEAM_CASE,
                    strength=Strength.WEAK,
                    seed_payout_id=seed.payout_id,
                    current_evidence=pending_refs(row),
                    historical_evidence=case_refs(seed),
                )
            )
    for seed in unresolved:
        if any(
            item.type == EvidenceType.TEAM and item.id == row.payout.team_id
            for item in seed.evidence
        ):
            leads.append(
                InvestigativeLead(
                    relationship=Relationship.UNRESOLVED_CASE,
                    strength=Strength.UNRESOLVED,
                    seed_payout_id=seed.seed_payout_id,
                    current_evidence=pending_refs(row),
                    historical_evidence=seed.evidence,
                )
            )
    matches = cluster_matches(all_paths, cluster_by_seed)
    captured_cards = tuple(
        item
        for item in attempts
        if item.status == TransactionStatus.CAPTURED and item.payment_method != PaymentMethod.ACH
    )
    assessable_cards = (
        bool(captured_cards)
        and all(card_key(item) for item in captured_cards)
        and any(card_key(attempt) for seed in seeds for attempt in seed.attempts)
    )
    if matches:
        status = Exposure.ESTABLISHED
        strongest = matches[0]
        seed_id = strongest.paths[0].seed_payout_id
        if strongest.strength == Strength.DIRECT:
            explanation = (
                f"A captured payment matches a card explicitly implicated in historical rejected "
                f"payout {seed_id}. Review the cited payment and case evidence."
            )
        else:
            explanation = (
                f"Captured activity on a different team shares both the owner phone and signup "
                f"device implicated in historical rejected payout {seed_id}. "
                "Review both verified links and the cited payments."
            )
    elif any(lead.strength == Strength.UNRESOLVED for lead in leads):
        status = Exposure.UNRESOLVED
        explanation = (
            "Review the cited non-payable attempt or the unresolved historical case evidence."
        )
    elif leads:
        status = Exposure.WEAK
        explanation = (
            "Review the cited incidental or profile associations for independent corroboration."
        )
    elif not assessable_cards:
        status = Exposure.INSUFFICIENT
        explanation = (
            "Available verified cases or captured card evidence are insufficient "
            "for a supported link."
        )
    else:
        status = Exposure.NONE
        explanation = (
            "The accepted attempts have no supported exposure to the verified cases "
            "in this snapshot."
        )
    return FraudFinding(
        payout_id=row.payout.payout_id,
        status=status,
        established_exposure=bool(matches),
        current_evidence=pending_refs(row, *attempts),
        primary=matches[0] if matches else None,
        alternatives=matches[1:],
        leads=tuple(sorted(leads, key=lambda item: (item.seed_payout_id, item.relationship))),
        explanation=explanation,
        policy_version=POLICY_VERSION,
        mapping_version=mapping_version,
    )
