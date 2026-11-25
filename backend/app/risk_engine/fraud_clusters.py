from hashlib import sha256

import networkx as nx

from app.risk_engine.fraud_evidence import Seed, case_refs, refs
from app.risk_engine.fraud_graph import merge_projection
from app.schemas.fraud_findings import ClusterSnapshot, NodeKind


def build_clusters(graph: nx.MultiGraph, seeds: tuple[Seed, ...]) -> tuple[ClusterSnapshot, ...]:
    by_id = {seed.payout_id: seed for seed in seeds}
    clusters = []
    for component in nx.connected_components(merge_projection(graph)):
        members = tuple(sorted(node.key for node in component if node.kind == NodeKind.CASE))
        canonical = min(
            (by_id[key] for key in members), key=lambda seed: (seed.known_at, seed.payout_id)
        )
        # Source IDs are constrained to exclude newlines. The key contains no identifier values.
        digest = sha256("\n".join(members).encode()).hexdigest()
        clusters.append(
            ClusterSnapshot(
                stable_case_key=f"case-v1-{digest}",
                seed_payout_id=canonical.payout_id,
                reason_code=canonical.reason,
                case_payout_ids=members,
                reason_codes=tuple(sorted({by_id[key].reason for key in members})),
                summary=f"Verified evidence supports {len(members)} historical rejection(s).",
                evidence=refs(
                    *((ref.type, ref.id) for key in members for ref in case_refs(by_id[key]))
                ),
            )
        )
    return tuple(sorted(clusters, key=lambda item: item.stable_case_key))
