from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import networkx as nx

from app.risk_engine.fraud_evidence import (
    Seed,
    card_key,
    case_refs,
    profile_keys,
    refs,
    visible_attempts,
)
from app.risk_engine.inputs import EvaluationInputs, PendingObservation
from app.schemas.fraud_findings import (
    EvidencePath,
    EvidenceRef,
    EvidenceStep,
    EvidenceType,
    NodeKind,
    PathNode,
    Relationship,
    Strength,
)


@dataclass(frozen=True, slots=True, repr=False)
class Node:
    kind: NodeKind
    key: str

    def safe(self) -> PathNode:
        source = (
            self.key
            if self.kind
            in {
                NodeKind.CASE,
                NodeKind.PAYOUT,
                NodeKind.TEAM,
                NodeKind.TRANSACTION,
            }
            else None
        )
        return PathNode(kind=self.kind, source_id=source)


@dataclass(frozen=True, slots=True)
class Edge:
    strength: Strength
    evidence: tuple[EvidenceRef, ...]
    known_at: datetime
    available_at: datetime
    seed_payout_id: str | None = None
    supports_exposure: bool = False


def pending_attempts(inputs: EvaluationInputs, row: PendingObservation):
    return visible_attempts(
        row, inputs.batch.cutoff, min(inputs.batch.imported_at, inputs.snapshot_at)
    )


def connect(
    graph: nx.MultiGraph, a: Node, b: Node, relationship: Relationship, witness: Edge
) -> None:
    graph.add_edge(a, b, key=relationship, witness=witness)


def build_graph(inputs: EvaluationInputs, seeds: tuple[Seed, ...]) -> nx.MultiGraph:
    graph = nx.MultiGraph()
    teams = {
        team.team_id: team
        for team in inputs.teams
        if team.provenance.imported_at <= inputs.snapshot_at
    }
    profiles = {team_id: profile_keys(team) for team_id, team in teams.items()}
    for team_id, team in teams.items():
        team_node = Node(NodeKind.TEAM, team_id)
        graph.add_node(team_node)
        stamp = Edge(
            Strength.WEAK,
            refs((EvidenceType.TEAM, team_id)),
            datetime.combine(team.onboarded_on, time.min, ZoneInfo("Asia/Kolkata")),
            team.provenance.imported_at,
        )
        for kind, value in profiles[team_id].items():
            connect(graph, team_node, Node(kind, value), Relationship.PROFILE_IDENTIFIER, stamp)

    for row in (*inputs.history, *inputs.pending):
        payout = row.payout
        if (
            payout.created_at > inputs.batch.cutoff
            or payout.provenance.imported_at > inputs.snapshot_at
        ):
            continue
        payout_node, team_node = (
            Node(NodeKind.PAYOUT, payout.payout_id),
            Node(NodeKind.TEAM, payout.team_id),
        )
        payout_stamp = Edge(
            Strength.CONTEXT,
            refs((EvidenceType.PAYOUT, payout.payout_id), (EvidenceType.TEAM, payout.team_id)),
            payout.created_at,
            payout.provenance.imported_at,
        )
        connect(graph, payout_node, team_node, Relationship.PAYOUT_TEAM, payout_stamp)
        attempts = (
            pending_attempts(inputs, row)
            if isinstance(row, PendingObservation)
            else visible_attempts(row, inputs.batch.cutoff, inputs.snapshot_at)
        )
        for attempt in attempts.values():
            txn = Node(NodeKind.TRANSACTION, attempt.txn_id)
            stamp = Edge(
                Strength.CONTEXT,
                refs(
                    (EvidenceType.TRANSACTION, attempt.txn_id),
                    (EvidenceType.PAYOUT, payout.payout_id),
                    (EvidenceType.TEAM, payout.team_id),
                ),
                attempt.created_at,
                attempt.provenance.imported_at,
            )
            connect(graph, payout_node, txn, Relationship.PAYOUT_ATTEMPT, stamp)
            connect(graph, txn, team_node, Relationship.ATTEMPT_TEAM, stamp)
            if (card := card_key(attempt)) is not None:
                connect(graph, txn, Node(NodeKind.CARD, card), Relationship.OBSERVED_CARD, stamp)

    for seed in seeds:
        case = Node(NodeKind.CASE, seed.payout_id)
        connect(
            graph,
            case,
            Node(NodeKind.PAYOUT, seed.payout_id),
            Relationship.CASE_PAYOUT,
            Edge(
                Strength.CONTEXT, case_refs(seed), seed.known_at, seed.available_at, seed.payout_id
            ),
        )
        for attempt in seed.attempts:
            stamp = Edge(
                Strength.DIRECT,
                case_refs(seed, attempt),
                seed.known_at,
                max(seed.available_at, attempt.provenance.imported_at),
                seed.payout_id,
                True,
            )
            connect(
                graph,
                case,
                Node(NodeKind.TRANSACTION, attempt.txn_id),
                Relationship.CASE_TRANSACTION,
                stamp,
            )
            if seed.implicates_payer_cards and (card := card_key(attempt)) is not None:
                # Keep the earliest availability when the same case explicitly names several uses.
                card_node = Node(NodeKind.CARD, card)
                edge = graph.get_edge_data(case, card_node, Relationship.IMPLICATED_CARD)
                if edge is None or stamp.available_at < edge["witness"].available_at:
                    connect(graph, case, card_node, Relationship.IMPLICATED_CARD, stamp)
        for kind in sorted(seed.implicated_profiles):
            team = teams[seed.team_id]
            witness = Edge(
                Strength.WEAK,
                case_refs(seed),
                seed.known_at,
                max(team.provenance.imported_at, seed.available_at),
                seed.payout_id,
            )
            connect(
                graph,
                case,
                Node(kind, profiles[seed.team_id][kind]),
                Relationship.IMPLICATED_PROFILE,
                witness,
            )
    return graph


def merge_projection(graph: nx.MultiGraph) -> nx.Graph:
    strong = nx.Graph()
    strong.add_nodes_from(node for node in graph if node.kind == NodeKind.CASE)
    for start, end, category, data in graph.edges(keys=True, data=True):
        if (
            category == Relationship.IMPLICATED_CARD
            and {start.kind, end.kind} == {NodeKind.CASE, NodeKind.CARD}
            and data["witness"].supports_exposure
        ):
            strong.add_edge(start, end)
    return strong


def evidence_path(
    graph: nx.MultiGraph, seed: Seed, hops: tuple[tuple[Node, Relationship, Node], ...]
) -> EvidencePath:
    steps = []
    for start, relationship, end in hops:
        witness: Edge = graph[start][end][relationship]["witness"]
        steps.append(
            EvidenceStep(
                start=start.safe(),
                end=end.safe(),
                relationship=relationship,
                strength=witness.strength,
                supports_exposure=witness.supports_exposure,
                seed_payout_id=witness.seed_payout_id,
                known_at=witness.known_at,
                available_at=witness.available_at,
                evidence=witness.evidence,
            )
        )
    return EvidencePath(seed_payout_id=seed.payout_id, steps=tuple(steps))
