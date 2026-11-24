from secrets import token_urlsafe
from threading import BoundedSemaphore

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.rate_limit import LoginRateLimiter
from app.core.review_scope import ActorContext, resolve_review_scope
from app.core.security import SessionClaims, SignedCredentials, hash_password, verify_password
from app.models.user import User
from app.models.values import UserRole
from app.repositories.users import find_by_id, find_by_username, increment_auth_version
from app.schemas.auth import LoginRequest


class LoginThrottled(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Login temporarily rate limited")
        self.retry_after = retry_after


class AuthService:
    def __init__(self, settings: Settings) -> None:
        if settings.auth_signing_secret is None:
            raise ValueError("An authentication signing secret is required")
        self.settings = settings
        self.credentials = SignedCredentials(
            settings.auth_signing_secret, settings.auth_session_seconds
        )
        self._rate_limiter = LoginRateLimiter(
            settings.login_rate_limit,
            settings.login_rate_window_seconds,
            settings.login_rate_max_entries,
        )
        self._verification_slots = BoundedSemaphore(4)
        self._dummy_hash = hash_password(token_urlsafe(32))

    def actor(self, user: User) -> ActorContext:
        return ActorContext(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            role=UserRole(user.role),
            auth_version=user.auth_version,
            scope_key=resolve_review_scope(self.settings.app_mode, user.user_id),
        )

    def authenticate(self, session: Session, login: LoginRequest, peer: str) -> ActorContext | None:
        retry_after = self._rate_limiter.retry_after(login.username, peer)
        if retry_after is not None:
            raise LoginThrottled(retry_after)
        if not self._verification_slots.acquire(blocking=False):
            raise LoginThrottled(1)
        try:
            user = find_by_username(session, login.username)
            password_hash = user.password_hash if user is not None else self._dummy_hash
            valid = verify_password(login.password.get_secret_value(), password_hash)
        finally:
            self._verification_slots.release()

        if not valid or user is None or not user.is_active:
            return None
        return self.actor(user)

    def resolve_actor(self, session: Session, claims: SessionClaims) -> ActorContext | None:
        user = find_by_id(session, claims.user_id)
        if user is None or not user.is_active or user.auth_version != claims.auth_version:
            return None
        return self.actor(user)

    def revoke_sessions(self, session: Session, actor: ActorContext) -> bool:
        if not increment_auth_version(session, actor.user_id, actor.auth_version):
            return False
        session.commit()
        return True
