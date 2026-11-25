from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import AppMode
from app.core.permissions import Permission, effective_permissions
from app.core.review_scope import ActorContext, resolve_review_scope
from app.models import PayoutDecision, PayoutDecisionHistory
from app.models.values import Decision
from app.repositories import payout_decisions as decisions
from app.risk_engine.override_evidence import validate_evidence
from app.schemas.decisions import (
    AuditStatus,
    DecisionActor,
    DecisionEvent,
    DecisionHistory,
    DecisionProblem,
    DecisionSource,
    DecisionVersion,
    EffectiveDecision,
    OverrideRequest,
)
from app.schemas.fraud_findings import EvidenceRef
from app.services.ingestion_service import dataset_scope


def version_content(
    original: PayoutDecision, event: PayoutDecisionHistory | None, actor_name: str | None
) -> DecisionVersion:
    if event is None:
        return DecisionVersion(
            decision=original.decision,
            decision_source=DecisionSource.AUTOMATED,
            cluster_id=original.cluster_id,
            reason=original.reason,
            evidence=tuple(EvidenceRef.model_validate(ref) for ref in original.evidence),
            actor=None,
            history_id=None,
            timestamp=original.created_at,
        )
    return DecisionVersion(
        decision=event.new_decision,
        decision_source=DecisionSource.HUMAN,
        cluster_id=event.cluster_id,
        reason=event.change_reason,
        evidence=tuple(EvidenceRef.model_validate(ref) for ref in event.evidence),
        actor=DecisionActor(user_id=event.changed_by, display_name=actor_name)
        if actor_name is not None
        else None,
        history_id=event.history_id,
        timestamp=event.created_at,
    )


def project_effective(
    original: PayoutDecision, event: PayoutDecisionHistory | None, actor_name: str | None
) -> EffectiveDecision:
    return EffectiveDecision(
        **version_content(original, event, actor_name).model_dump(),
        run_id=original.run_id,
        payout_id=original.payout_id,
        decision_id=original.decision_id,
        original_decision=original.decision,
        effective_version=event.version if event else 1,
    )


def _effective_row(
    session: Session, mode: AppMode, actor: ActorContext, run_id: UUID, payout_id: str
) -> tuple[PayoutDecision, PayoutDecisionHistory | None, str | None]:
    scope = resolve_review_scope(mode, actor.user_id)
    query = decisions.effective_decisions(run_id, dataset_scope(mode), scope)
    row = session.execute(query.where(PayoutDecision.payout_id == payout_id)).one_or_none()
    if row is None:
        raise DecisionProblem("decision_not_found", 404)
    return row[0], row[1], row[2]


def get_history(
    session: Session, mode: AppMode, actor: ActorContext, run_id: UUID, payout_id: str
) -> DecisionHistory:
    if Permission.VIEW_AUDIT not in effective_permissions(actor.role, mode):
        raise DecisionProblem("forbidden", 403)
    original, latest, latest_actor = _effective_row(session, mode, actor, run_id, payout_id)
    effective = project_effective(original, latest, latest_actor)
    audit = [
        DecisionEvent(
            **version_content(original, None, None).model_dump(),
            version=1,
            previous_decision=None,
            status=AuditStatus.CURRENT if latest is None else AuditStatus.SUPERSEDED,
        )
    ]
    # Pin the second read to the version already selected. Earlier events are immutable.
    for event, event_actor in decisions.scoped_events(
        session,
        original.decision_id,
        resolve_review_scope(mode, actor.user_id),
        through=effective.effective_version,
    ):
        audit.append(
            DecisionEvent(
                **version_content(original, event, event_actor).model_dump(),
                version=event.version,
                previous_decision=event.previous_decision,
                status=(
                    AuditStatus.CURRENT
                    if event.version == effective.effective_version
                    else AuditStatus.SUPERSEDED
                ),
            )
        )
    return DecisionHistory(effective=effective, history=tuple(audit))


def override_decision(
    session: Session, mode: AppMode, actor: ActorContext, payout_id: str, request: OverrideRequest
) -> EffectiveDecision:
    if Permission.OVERRIDE_DECISIONS not in effective_permissions(actor.role, mode):
        raise DecisionProblem("forbidden", 403)
    with session.begin():
        session.connection(execution_options={"isolation_level": "READ COMMITTED"})
        target = decisions.lock_original(session, request.run_id, payout_id, dataset_scope(mode))
        if target is None:
            raise DecisionProblem("decision_not_found", 404)
        original, manifest, team_id = target
        # Read in a new statement so we see any override committed by the previous lock holder.
        current = project_effective(
            *_effective_row(session, mode, actor, request.run_id, payout_id)
        )
        if request.expected_version != current.effective_version:
            raise DecisionProblem("stale_decision_version", 409)
        if request.decision == current.decision:
            raise DecisionProblem("unchanged_decision")
        if (request.decision == Decision.REJECT) != (request.cluster_id is not None):
            raise DecisionProblem("invalid_override_cluster")
        cluster = (
            decisions.run_cluster(session, request.run_id, request.cluster_id)
            if request.cluster_id is not None
            else None
        )
        if request.cluster_id is not None and cluster is None:
            raise DecisionProblem("invalid_override_cluster")
        evidence = tuple(EvidenceRef(type=ref.type, id=ref.id) for ref in request.evidence)
        validate_evidence(
            request.decision,
            evidence,
            payout_id,
            team_id,
            manifest,
            original.findings,
            original.evidence,
            cluster.stable_case_key if cluster else None,
        )
        decisions.append_override(
            session,
            decision_id=original.decision_id,
            scope_key=resolve_review_scope(mode, actor.user_id),
            version=current.effective_version + 1,
            previous_decision=current.decision,
            new_decision=request.decision,
            cluster_id=request.cluster_id,
            change_reason=request.reason,
            evidence=[ref.model_dump(mode="json") for ref in evidence],
            changed_by=actor.user_id,
        )
        updated = project_effective(
            *_effective_row(session, mode, actor, request.run_id, payout_id)
        )
    return updated
