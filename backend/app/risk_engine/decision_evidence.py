from collections import defaultdict
from collections.abc import Iterable

from app.models.values import TransactionStatus
from app.risk_engine.fraud_evidence import card_key, case_refs, resolve_seeds
from app.risk_engine.hybrid_decisions import statistical_evidence
from app.risk_engine.inputs import EvaluationInputs, HistoricalObservation, PendingObservation
from app.schemas.evaluation import EvaluationError
from app.schemas.fraud_findings import (
    ClusterMatch,
    ClusterSnapshot,
    EvidenceRef,
    EvidenceType,
    Exposure,
    FraudFinding,
    NodeKind,
    PathNode,
    Relationship,
    Strength,
)
from app.schemas.ml_findings import MLFinding
from app.services.statistical_analysis_service import ObservationAnalysis

DIRECT_PATH = (
    Relationship.PAYOUT_ATTEMPT,
    Relationship.OBSERVED_CARD,
    Relationship.IMPLICATED_CARD,
    Relationship.CASE_TRANSACTION,
    Relationship.PAYOUT_ATTEMPT,
)
PROFILE_PATH = (
    Relationship.PAYOUT_ATTEMPT,
    Relationship.ATTEMPT_TEAM,
    Relationship.PROFILE_IDENTIFIER,
    Relationship.IMPLICATED_PROFILE,
    Relationship.CASE_TRANSACTION,
    Relationship.PAYOUT_ATTEMPT,
)
SOURCE_TYPES = {
    NodeKind.CASE: EvidenceType.PAYOUT,
    NodeKind.PAYOUT: EvidenceType.PAYOUT,
    NodeKind.TRANSACTION: EvidenceType.TRANSACTION,
    NodeKind.TEAM: EvidenceType.TEAM,
}
CORROBORATION = {NodeKind.PHONE, NodeKind.DEVICE}


def require(condition: object) -> None:
    if not condition:
        raise EvaluationError("invalid_evaluation_evidence")


def source_refs(row: HistoricalObservation | PendingObservation) -> set[EvidenceRef]:
    return {
        EvidenceRef(type=EvidenceType.PAYOUT, id=row.payout.payout_id),
        EvidenceRef(type=EvidenceType.TEAM, id=row.payout.team_id),
        *(EvidenceRef(type=EvidenceType.TRANSACTION, id=txn.txn_id) for txn in row.attempts),
    }


class DecisionEvidence:
    def __init__(self, inputs: EvaluationInputs, clusters: tuple[ClusterSnapshot, ...]) -> None:
        self.inputs = inputs
        self.history = {row.payout.payout_id: row for row in inputs.history}
        self.seeds = {row.payout.payout_id: row for row in inputs.fraud_candidates}
        self.reconstructed_refs = {
            seed.payout_id: set(case_refs(seed))
            for seed in resolve_seeds(inputs)[0]
            if seed.witnesses
        }
        self.history_refs = set().union(*(source_refs(row) for row in inputs.history))
        self.clusters = {item.stable_case_key: item for item in clusters}
        require(len(self.clusters) == len(clusters))
        used_seeds: set[str] = set()
        for cluster in clusters:
            ids = set(cluster.case_payout_ids)
            require(ids <= self.seeds.keys() and not ids & used_seeds)
            require(cluster.seed_payout_id in ids and bool(cluster.evidence))
            require(
                all(
                    EvidenceRef(type=EvidenceType.PAYOUT, id=key) in cluster.evidence for key in ids
                )
            )
            require(
                cluster.reason_code == self.seeds[cluster.seed_payout_id].disposition.reason_code
            )
            self.check(cluster.evidence, set().union(*(self.case_source_refs(key) for key in ids)))
            used_seeds.update(ids)

    def case_source_refs(self, key: str) -> set[EvidenceRef]:
        return source_refs(self.seeds[key]) | self.reconstructed_refs.get(key, set())

    @staticmethod
    def check(refs: Iterable[EvidenceRef], allowed: set[EvidenceRef]) -> None:
        require(all(ref in allowed for ref in refs))

    @staticmethod
    def check_node(node: PathNode, allowed: set[EvidenceRef]) -> None:
        if (kind := SOURCE_TYPES.get(node.kind)) is None:
            require(node.source_id is None)
        else:
            require(node.source_id is not None)
            require(EvidenceRef(type=kind, id=node.source_id) in allowed)

    def check_match(self, row: PendingObservation, match: ClusterMatch) -> None:
        cluster = self.clusters.get(match.stable_case_key)
        require(cluster is not None and match.paths)
        require(set(match.supporting_seed_ids) == {path.seed_payout_id for path in match.paths})
        require(set(match.supporting_seed_ids) <= set(cluster.case_payout_ids))
        current = source_refs(row)
        historical = set().union(*(self.case_source_refs(key) for key in match.supporting_seed_ids))
        self.check(match.current_evidence, current)
        self.check(match.historical_evidence, historical)
        direct = False
        profiles: dict[tuple[str, str, str], set[NodeKind]] = defaultdict(set)
        captures = {
            txn.txn_id: txn for txn in row.attempts if txn.status == TransactionStatus.CAPTURED
        }
        for path in match.paths:
            seed = self.seeds[path.seed_payout_id]
            steps = path.steps
            relationships = tuple(step.relationship for step in steps)
            require(relationships in (DIRECT_PATH, PROFILE_PATH))
            require(
                steps[0].start == PathNode(kind=NodeKind.PAYOUT, source_id=row.payout.payout_id)
            )
            require(steps[-1].end == PathNode(kind=NodeKind.PAYOUT, source_id=path.seed_payout_id))
            require(all(a.end == b.start for a, b in zip(steps, steps[1:], strict=False)))
            allowed = current | self.case_source_refs(path.seed_payout_id)
            for step in steps:
                require(step.known_at <= self.inputs.batch.cutoff)
                require(step.available_at <= self.inputs.snapshot_at)
                require(step.seed_payout_id in (None, path.seed_payout_id))
                self.check_node(step.start, allowed)
                self.check_node(step.end, allowed)
                self.check(step.evidence, allowed)
            current_id, historic_id = steps[0].end.source_id, steps[-1].start.source_id
            historical_attempt = next(
                (txn for txn in seed.attempts if txn.txn_id == historic_id), None
            )
            require(current_id in captures and historical_attempt is not None)
            require(
                EvidenceRef(type=EvidenceType.TRANSACTION, id=current_id) in match.current_evidence
            )
            require(
                EvidenceRef(type=EvidenceType.TRANSACTION, id=historic_id)
                in match.historical_evidence
            )
            require(
                EvidenceRef(type=EvidenceType.PAYOUT, id=path.seed_payout_id)
                in match.historical_evidence
            )
            if relationships == DIRECT_PATH:
                require(steps[2].supports_exposure and steps[2].start.kind == NodeKind.CARD)
                key = card_key(captures[current_id])
                require(key is not None and key == card_key(historical_attempt))
                direct = True
            else:
                require(steps[3].supports_exposure and steps[3].start.kind in CORROBORATION)
                require(historical_attempt.status == TransactionStatus.CAPTURED)
                profiles[path.seed_payout_id, current_id, historic_id].add(steps[3].start.kind)
        require(all(categories == CORROBORATION for categories in profiles.values()))
        expected = (
            (Relationship.DIRECT_CAPTURE, Strength.DIRECT)
            if direct
            else (Relationship.CORROBORATED_PROFILE, Strength.CORROBORATED)
        )
        require((match.relationship, match.strength) == expected)

    def check_findings(
        self,
        row: PendingObservation,
        stats: ObservationAnalysis,
        fraud: FraudFinding,
        ml: MLFinding,
    ) -> None:
        current = source_refs(row)
        allowed = current | self.history_refs
        for pair in stats.comparisons:
            for item in (pair.team, pair.profession):
                require(item.current_payout_id == row.payout.payout_id)
                self.check(statistical_evidence(item), allowed)
                self.check(
                    (
                        EvidenceRef(type=EvidenceType.TRANSACTION, id=key)
                        for key in item.current_transaction_ids
                    ),
                    current,
                )
        self.check(ml.evidence, allowed)
        for item in ml.unusual_features:
            self.check(item.evidence, allowed)
        self.check(fraud.current_evidence, current)
        require(
            (fraud.status == Exposure.ESTABLISHED)
            == fraud.established_exposure
            == (fraud.primary is not None)
        )
        require(not fraud.alternatives or fraud.primary is not None)
        for match in (fraud.primary, *fraud.alternatives):
            if match is not None:
                self.check_match(row, match)
        for lead in fraud.leads:
            require(lead.seed_payout_id in self.seeds)
            self.check(lead.current_evidence, current)
            self.check(lead.historical_evidence, self.case_source_refs(lead.seed_payout_id))
        require(
            (fraud.status != Exposure.UNRESOLVED)
            or any(lead.strength == Strength.UNRESOLVED for lead in fraud.leads)
        )
