from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from typing import Annotated, Self
from uuid import UUID

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from itsdangerous import BadData, URLSafeSerializer
from itsdangerous.encoding import base64_decode, base64_encode
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
CSRF_SECONDS = 600
MAX_TOKEN_LENGTH = 2048

_password_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1, type=Type.ID)


def hash_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"Passwords must be {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters"
        )
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not 1 <= len(password) <= PASSWORD_MAX_LENGTH or not password_hash.startswith("$argon2id$"):
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError, UnicodeError):
        return False


def timestamp(now: datetime | None) -> int:
    return int((now if now is not None else datetime.now(UTC)).timestamp())


class TimedClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    issued_at: Annotated[int, Field(strict=True, ge=0)]
    expires_at: Annotated[int, Field(strict=True, ge=0)]

    @classmethod
    def read(
        cls, serializer: URLSafeSerializer, token: str, *, now: datetime | None, max_age: int
    ) -> Self | None:
        if not token or len(token) > MAX_TOKEN_LENGTH or not token.isascii():
            return None
        try:
            encoded_signature = token.rpartition(".")[2].encode("ascii")
            if base64_encode(base64_decode(encoded_signature)) != encoded_signature:
                return None
            claims = cls.model_validate(serializer.loads(token))
        except (BadData, ValidationError):
            return None

        if (
            claims.issued_at <= timestamp(now) < claims.expires_at
            and 0 < claims.expires_at - claims.issued_at <= max_age
        ):
            return claims
        return None


class SessionClaims(TimedClaims):
    user_id: UUID
    auth_version: Annotated[int, Field(strict=True, ge=1)]
    nonce: Annotated[str, Field(strict=True, min_length=32, max_length=64)]


class CsrfClaims(TimedClaims):
    binding: Annotated[str, Field(strict=True, pattern=r"^(anonymous|[0-9a-f]{64})$")]
    nonce: Annotated[str, Field(strict=True, min_length=32, max_length=64)]


def session_binding(session_token: str | None) -> str:
    if session_token:
        return sha256(session_token.encode()).hexdigest()
    return "anonymous"


class SignedCredentials:
    def __init__(self, secret: SecretStr, session_seconds: int) -> None:
        key = secret.get_secret_value()
        signer_options = {"digest_method": sha256, "key_derivation": "hmac"}
        self._session = URLSafeSerializer(
            key, salt="scanpay-session-v1", signer_kwargs=signer_options
        )
        self._csrf = URLSafeSerializer(key, salt="scanpay-csrf-v1", signer_kwargs=signer_options)
        self.session_seconds = session_seconds

    def issue_session(
        self, user_id: UUID, auth_version: int, *, now: datetime | None = None
    ) -> str:
        issued_at = timestamp(now)
        claims = SessionClaims(
            user_id=user_id,
            auth_version=auth_version,
            issued_at=issued_at,
            expires_at=issued_at + self.session_seconds,
            nonce=token_urlsafe(24),
        )
        return self._session.dumps(claims.model_dump(mode="json"))

    def read_session(self, token: str, *, now: datetime | None = None) -> SessionClaims | None:
        return SessionClaims.read(self._session, token, now=now, max_age=self.session_seconds)

    def issue_csrf(self, session_token: str | None, *, now: datetime | None = None) -> str:
        issued_at = timestamp(now)
        claims = CsrfClaims(
            binding=session_binding(session_token),
            issued_at=issued_at,
            expires_at=issued_at + CSRF_SECONDS,
            nonce=token_urlsafe(24),
        )
        return self._csrf.dumps(claims.model_dump())

    def valid_csrf(
        self,
        header: str,
        cookie: str,
        session_token: str | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        if len(header) > MAX_TOKEN_LENGTH or not header.isascii() or not cookie.isascii():
            return False
        if not compare_digest(header, cookie):
            return False
        claims = CsrfClaims.read(self._csrf, cookie, now=now, max_age=CSRF_SECONDS)
        return claims is not None and compare_digest(claims.binding, session_binding(session_token))
