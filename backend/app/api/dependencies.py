from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.auth_cookies import cookie_name
from app.core.permissions import Permission, effective_permissions
from app.core.review_scope import ActorContext
from app.database.session import Database
from app.services.auth_service import AuthService

PUBLIC_API_ROUTES = {("GET", "/api/auth/csrf"), ("POST", "/api/auth/login")}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_api_request(request: Request) -> bool:
    return request.url.path == "/api" or request.url.path.startswith("/api/")


async def private_api_response(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    if is_api_request(request):
        response.headers["Cache-Control"] = "private, no-store"
    return response


def get_database(request: Request) -> Database | None:
    return request.app.state.database


def get_session(request: Request) -> Iterator[Session]:
    database = get_database(request)
    if database is None:
        raise HTTPException(status_code=503)
    with database.session() as session:
        yield session


def get_auth_service(request: Request) -> AuthService:
    auth = request.app.state.auth
    if auth is None:
        raise HTTPException(status_code=503)
    return auth


def require_trusted_origin(request: Request, auth: AuthService) -> None:
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] not in auth.settings.trusted_origins:
        raise HTTPException(status_code=403)


def require_csrf(request: Request) -> None:
    auth = get_auth_service(request)
    require_trusted_origin(request, auth)
    headers = request.headers.getlist("x-csrf-token")
    cookie = request.cookies.get(cookie_name(auth.settings, csrf=True), "")
    session = request.cookies.get(cookie_name(auth.settings))
    if len(headers) != 1 or not auth.credentials.valid_csrf(headers[0], cookie, session):
        raise HTTPException(status_code=403)


def enforce_api_security(request: Request) -> None:
    """Authenticate every matching /api route except the explicit bootstrap and login routes."""
    if not is_api_request(request):
        return
    if (request.method, request.url.path) in PUBLIC_API_ROUTES:
        if request.method == "POST":
            require_csrf(request)
        elif "origin" in request.headers:
            require_trusted_origin(request, get_auth_service(request))
        return

    auth: AuthService | None = request.app.state.auth
    if auth is None:
        raise HTTPException(status_code=401)
    token = request.cookies.get(cookie_name(auth.settings), "")
    claims = auth.credentials.read_session(token)
    if claims is None:
        raise HTTPException(status_code=401)
    database = get_database(request)
    if database is None:
        raise HTTPException(status_code=503)
    with database.session() as session:
        actor = auth.resolve_actor(session, claims)
    if actor is None:
        raise HTTPException(status_code=401)
    request.state.actor = actor
    if request.method not in SAFE_METHODS:
        require_csrf(request)


def get_current_user(request: Request) -> ActorContext:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise HTTPException(status_code=401)
    return actor


CurrentUser = Annotated[ActorContext, Depends(get_current_user)]


def get_review_scope(actor: CurrentUser) -> str:
    return actor.scope_key


def require_permission(permission: Permission) -> Callable[..., ActorContext]:
    def check_permission(
        actor: CurrentUser, auth: Annotated[AuthService, Depends(get_auth_service)]
    ) -> ActorContext:
        if permission not in effective_permissions(actor.role, auth.settings.app_mode):
            raise HTTPException(status_code=403)
        return actor

    return check_permission


require_admin = require_permission(Permission.RUN_EVALUATIONS)
require_analyst_or_admin = require_permission(Permission.OVERRIDE_DECISIONS)
