from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import EvaluationRun, FraudCluster, ModelVersion, PayoutDecision
from app.risk_engine.hybrid_decisions import DecisionDraft
from app.schemas.evaluation import EvaluationError
from app.schemas.fraud_findings import ClusterSnapshot
from app.schemas.ml_findings import ModelVersionDraft


def store_model(session: Session, draft: ModelVersionDraft) -> UUID | None:
    if not draft.training_observation_count:
        return None
    fields = draft.persistence_fields()
    inserted = session.scalar(
        insert(ModelVersion)
        .values(**fields)
        .on_conflict_do_nothing(
            index_elements=[
                ModelVersion.scope_key,
                ModelVersion.training_fingerprint,
                ModelVersion.config,
            ]
        )
        .returning(ModelVersion.model_version_id)
    )
    if inserted is not None:
        return inserted
    existing = session.scalar(
        select(ModelVersion).where(
            ModelVersion.scope_key == draft.scope_key,
            ModelVersion.training_fingerprint == draft.training_fingerprint,
            ModelVersion.config == draft.config,
        )
    )
    if existing is None or any(getattr(existing, name) != value for name, value in fields.items()):
        raise EvaluationError("incompatible_model_metadata", 409)
    return existing.model_version_id


def store_clusters(
    session: Session, run: EvaluationRun, clusters: tuple[ClusterSnapshot, ...]
) -> dict[str, UUID]:
    rows = [
        FraudCluster(
            run_id=run.run_id,
            stable_case_key=cluster.stable_case_key,
            seed_payout_id=cluster.seed_payout_id,
            reason_code=cluster.reason_code,
            summary=cluster.summary,
            evidence=[ref.model_dump(mode="json") for ref in cluster.evidence],
        )
        for cluster in clusters
    ]
    session.add_all(rows)
    session.flush()
    return {row.stable_case_key: row.cluster_id for row in rows}


def store_decisions(
    session: Session,
    run: EvaluationRun,
    drafts: tuple[DecisionDraft, ...],
    clusters: dict[str, UUID],
    model_id: UUID | None,
) -> None:
    rows = [
        PayoutDecision(
            run_id=run.run_id,
            payout_id=draft.payout_id,
            decision=draft.decision,
            cluster_id=clusters[draft.cluster_key] if draft.cluster_key else None,
            reason=draft.reason,
            evidence=[ref.model_dump(mode="json") for ref in draft.evidence],
            findings={
                "team_id": draft.team_id,
                "statistics": [
                    {
                        "team": pair.team.model_dump(mode="json"),
                        "profession": pair.profession.model_dump(mode="json"),
                        "preferred_scope": pair.preferred_scope,
                    }
                    for pair in draft.statistics.comparisons
                ],
                "fraud": draft.fraud.model_dump(mode="json"),
                "ml": draft.ml.model_dump(mode="json"),
                "configuration": run.history_manifest["configuration"],
            },
            ml_status=draft.ml.status,
            ml_score=draft.ml.anomaly_score,
            ml_is_anomaly=draft.ml.is_anomaly,
            model_version_id=model_id,
            rule_version=run.rule_version,
        )
        for draft in drafts
    ]
    session.add_all(rows)
    session.flush()
