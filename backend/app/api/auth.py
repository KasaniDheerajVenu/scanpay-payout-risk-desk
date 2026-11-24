from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.types import Message

from app.api.auth_cookies import clear_credential_cookie, cookie_name, set_credential_cookie
from app.api.dependencies import get_auth_service, get_current_user, get_session
from app.core.permissions import effective_permissions
from app.core.review_scope import ActorContext
from app.core.security import CSRF_SECONDS
from app.schemas.auth import CsrfResponse, LoginRequest, UserResponse
from app.services.auth_service import AuthService, LoginThrottled


class AuthRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()
        if not self.path.endswith("/login"):
            return handler

        async def bounded_login(request: Request) -> Response:
            body_size = 0

            async def receive() -> Message:
                nonlocal body_size
                message = await request.receive()
                if message["type"] == "http.request":
                    body_size += len(message.get("body", b""))
                    if body_size > 4096:
                        raise HTTPException(status_code=413)
                return message

            return await handler(Request(request.scope, receive=receive))

        return bounded_login


router = APIRouter(prefix="/api/auth", tags=["Authentication"], route_class=AuthRoute)
Auth = Annotated[AuthService, Depends(get_auth_service)]


def user_response(actor: ActorContext, auth: AuthService) -> UserResponse:
    return UserResponse(
        user_id=actor.user_id,
        username=actor.username,
        display_name=actor.display_name,
        role=actor.role,
        mode=auth.settings.app_mode,
        permissions=sorted(effective_permissions(actor.role, auth.settings.app_mode)),
    )


@router.get("/csrf")
def csrf_token(request: Request, response: Response, auth: Auth) -> CsrfResponse:
    session_token = request.cookies.get(cookie_name(auth.settings))
    existing = request.cookies.get(cookie_name(auth.settings, csrf=True))
    if existing and auth.credentials.valid_csrf(existing, existing, session_token):
        return CsrfResponse(csrf_token=existing)
    token = auth.credentials.issue_csrf(session_token)
    set_credential_cookie(response, auth.settings, token, CSRF_SECONDS, csrf=True)
    return CsrfResponse(csrf_token=token)


@router.post("/login")
def login(
    login: LoginRequest,
    request: Request,
    response: Response,
    auth: Auth,
    session: Annotated[Session, Depends(get_session)],
) -> UserResponse:
    peer = request.client.host if request.client is not None else "unknown"
    try:
        actor = auth.authenticate(session, login, peer)
    except LoginThrottled as exc:
        raise HTTPException(
            status_code=429, headers={"Retry-After": str(exc.retry_after)}
        ) from None
    if actor is None:
        raise HTTPException(status_code=401)

    token = auth.credentials.issue_session(actor.user_id, actor.auth_version)
    set_credential_cookie(response, auth.settings, token, auth.settings.auth_session_seconds)
    clear_credential_cookie(response, auth.settings, csrf=True)
    return user_response(actor, auth)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    auth: Auth,
    actor: Annotated[ActorContext, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> None:
    if not auth.revoke_sessions(session, actor):
        raise HTTPException(status_code=401)
    clear_credential_cookie(response, auth.settings)
    clear_credential_cookie(response, auth.settings, csrf=True)


@router.get("/me")
def me(actor: Annotated[ActorContext, Depends(get_current_user)], auth: Auth) -> UserResponse:
    return user_response(actor, auth)
