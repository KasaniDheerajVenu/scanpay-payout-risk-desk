from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, require_analyst_or_admin, require_permission
from app.core.errors import error_response
from app.core.permissions import Permission
from app.core.review_scope import ActorContext
from app.schemas.decisions import (
    SOURCE_ID_PATTERN,
    DecisionHistory,
    DecisionProblem,
    EffectiveDecision,
    OverrideRequest,
)
from app.services.decision_overrides import get_history, override_decision

router = APIRouter(prefix="/api/payouts", tags=["decisions"])
require_audit = require_permission(Permission.VIEW_AUDIT)
PayoutId = Annotated[str, Path(pattern=SOURCE_ID_PATTERN)]


@router.post("/{payout_id}/overrides", status_code=201, response_model=EffectiveDecision)
def post_override(
    payout_id: PayoutId,
    payload: OverrideRequest,
    request: Request,
    actor: Annotated[ActorContext, Depends(require_analyst_or_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> EffectiveDecision | Response:
    try:
        return override_decision(
            session, request.app.state.settings.app_mode, actor, payout_id, payload
        )
    except DecisionProblem as exc:
        return error_response(exc.status, exc.code, HTTPStatus(exc.status).phrase)


@router.get("/{payout_id}/history", response_model=DecisionHistory)
def history(
    payout_id: PayoutId,
    run_id: Annotated[UUID, Query()],
    request: Request,
    actor: Annotated[ActorContext, Depends(require_audit)],
    session: Annotated[Session, Depends(get_session)],
) -> DecisionHistory | Response:
    try:
        return get_history(session, request.app.state.settings.app_mode, actor, run_id, payout_id)
    except DecisionProblem as exc:
        return error_response(exc.status, exc.code, HTTPStatus(exc.status).phrase)
