from collections import defaultdict
from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID

from app.core.review_scope import ActorContext
from app.database.session import Database
from app.models.values import TransactionStatus
from app.risk_engine.eligibility import historical_eligibility
from app.risk_engine.feature_schema import FEATURE_VERSION, StatisticalPolicy
from app.risk_engine.fraud_clusters import build_clusters
from app.risk_engine.fraud_evidence import (
    POLICY_VERSION,
    CaseMapping,
    Seed,
    card_key,
    mapping_index,
    resolve_seeds,
    visible_attempts,
)
from app.risk_engine.fraud_graph import build_graph
from app.risk_engine.fraud_rules import assess_pending
from app.risk_engine.inputs import Attempt, Eligibility, EvaluationConfig, EvaluationInputs
from app.schemas.fraud_findings import ClusterSnapshot, FraudFinding, UnresolvedSeed
from app.services.evaluation_input_service import capture_inputs
from app.services.evaluation_manifest import canonical_json, fingerprint
from app.services.statistical_analysis_service import (
    FraudExclusion,
    StatisticalAnalysis,
    analyze_inputs,
    evaluation_config,
)


@dataclass(frozen=True, slots=True)
class Contamination:
    exclusion: FraudExclusion
    seed_payout_id: str
    transaction_id: str
    implicated_transaction_id: str


@dataclass(frozen=True, slots=True)
class FraudAnalysis:
    input_digest: str
    policy_version: str
    mapping_version: str | None
    clusters: tuple[ClusterSnapshot, ...]
    unresolved_seeds: tuple[UnresolvedSeed, ...]
    pending: tuple[FraudFinding, ...]
    contamination: tuple[Contamination, ...]


@dataclass(frozen=True, slots=True)
class FraudAndStatistics:
    fraud: FraudAnalysis
    statistics: StatisticalAnalysis


def fraud_config(base: EvaluationConfig, mapping: CaseMapping | None = None) -> EvaluationConfig:
    mapped = mapping_index(mapping)
    digest = None
    if mapping is not None:
        payload = {
            "version": mapping.version,
            "cases": [
                {
                    "seed": key,
                    "transactions": sorted(case.transaction_ids),
                    "available_at": case.available_at,
                    "profile_categories": sorted(case.implicated_profile_kinds),
                }
                for key, case in sorted(mapped.items())
            ],
        }
        digest = sha256(canonical_json(payload).encode()).hexdigest()
    config = base.model_copy(
        update={
            "fraud_policy_version": POLICY_VERSION,
            "fraud_mapping_version": mapping.version if mapping else None,
            "fraud_mapping_digest": digest,
        }
    )
    if base.fraud_policy_version is not None and config != base:
        raise ValueError("fraud_policy_fingerprint_mismatch")
    return config


def contamination_for_history(
    inputs: EvaluationInputs, seeds: tuple[Seed, ...]
) -> tuple[Contamination, ...]:
    by_card: dict[str, list[tuple[Seed, Attempt]]] = defaultdict(list)
    for seed in seeds:
        for source in seed.attempts:
            if (card := card_key(source)) is not None:
                by_card[card].append((seed, source))

    output = []
    for row in sorted(inputs.history, key=lambda item: item.payout.payout_id):
        if (
            row.payout.provenance.imported_at > inputs.snapshot_at
            or historical_eligibility(
                row.disposition, inputs.batch.cutoff, inputs.snapshot_at, True
            )
            != Eligibility.NORMAL_CANDIDATE
        ):
            continue
        attempts = visible_attempts(row, inputs.batch.cutoff, inputs.snapshot_at)
        for key, attempt in sorted(attempts.items()):
            if attempt.status != TransactionStatus.CAPTURED or (card := card_key(attempt)) is None:
                continue
            for seed, source in by_card[card]:
                if source.created_at > attempt.created_at:
                    continue
                output.append(
                    Contamination(
                        FraudExclusion(
                            row.payout.payout_id,
                            max(seed.known_at, attempt.created_at),
                            max(
                                seed.available_at,
                                source.provenance.imported_at,
                                attempt.provenance.imported_at,
                                row.payout.provenance.imported_at,
                            ),
                        ),
                        seed.payout_id,
                        key,
                        source.txn_id,
                    )
                )
    return tuple(output)


def analyze_fraud(inputs: EvaluationInputs, mapping: CaseMapping | None = None) -> FraudAnalysis:
    config = fraud_config(inputs.config, mapping)
    seeds, unresolved = resolve_seeds(inputs, mapping)
    graph = build_graph(inputs, seeds)
    clusters = build_clusters(graph, seeds)
    by_seed = {seed: cluster for cluster in clusters for seed in cluster.case_payout_ids}
    pending = tuple(
        assess_pending(inputs, graph, row, seeds, by_seed, unresolved, config.fraud_mapping_version)
        for row in sorted(inputs.pending, key=lambda item: item.payout.payout_id)
    )
    return FraudAnalysis(
        fingerprint(replace(inputs, config=config)),
        POLICY_VERSION,
        config.fraud_mapping_version,
        clusters,
        unresolved,
        pending,
        contamination_for_history(inputs, seeds),
    )


def analyze_fraud_and_statistics(
    inputs: EvaluationInputs,
    policy: StatisticalPolicy | None = None,
    mapping: CaseMapping | None = None,
) -> FraudAndStatistics:
    policy = policy or StatisticalPolicy()
    statistics_config = evaluation_config(policy, inputs.config)
    if inputs.config.feature_version not in (None, FEATURE_VERSION) or (
        inputs.config.statistics_digest is not None and inputs.config != statistics_config
    ):
        raise ValueError("statistical_policy_fingerprint_mismatch")
    configured = replace(inputs, config=fraud_config(statistics_config, mapping))
    fraud = analyze_fraud(configured, mapping)
    statistics = analyze_inputs(
        configured, policy, exclusions=tuple(item.exclusion for item in fraud.contamination)
    )
    return FraudAndStatistics(fraud, statistics)


def capture_fraud_analysis(
    database: Database,
    actor: ActorContext,
    batch_id: UUID,
    policy: StatisticalPolicy | None = None,
    mapping: CaseMapping | None = None,
) -> FraudAndStatistics:
    policy = policy or StatisticalPolicy()
    config = fraud_config(evaluation_config(policy), mapping)
    inputs = capture_inputs(database, actor, batch_id, config)
    return analyze_fraud_and_statistics(inputs, policy, mapping)
