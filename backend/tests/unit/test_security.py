from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from itsdangerous import URLSafeSerializer
from itsdangerous.encoding import base64_decode
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.rate_limit import LoginRateLimiter
from app.core.security import SignedCredentials, hash_password, verify_password

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_passwords_are_salted_argon2id_and_verified() -> None:
    password = "fictional long password"
    first = hash_password(password)
    second = hash_password(password)
    assert first.startswith("$argon2id$")
    assert first != second
    assert verify_password(password, first)
    assert not verify_password("wrong password", first)
    assert not verify_password(password, "malformed-hash")
    assert not verify_password("x" * 129, first)


@pytest.mark.parametrize("password", ["short", "x" * 129])
def test_provisioning_password_length(password: str) -> None:
    with pytest.raises(ValueError, match="characters"):
        hash_password(password)


def test_session_claims_are_signed_expiring_and_unique() -> None:
    secret = SecretStr(token_urlsafe(32))
    credentials = SignedCredentials(secret, 60)
    user_id = uuid4()
    token = credentials.issue_session(user_id, 2, now=NOW)
    claims = credentials.read_session(token, now=NOW + timedelta(seconds=59))
    assert claims is not None
    assert claims.user_id == user_id and claims.auth_version == 2
    assert claims.expires_at - claims.issued_at == 60
    assert token != credentials.issue_session(user_id, 2, now=NOW)
    assert credentials.read_session(token, now=NOW + timedelta(seconds=60)) is None
    assert credentials.read_session(token, now=NOW - timedelta(seconds=1)) is None
    assert SignedCredentials(SecretStr(token_urlsafe(32)), 60).read_session(token, now=NOW) is None


def test_tampered_and_incomplete_signed_sessions_are_invalid() -> None:
    secret = SecretStr(token_urlsafe(32))
    credentials = SignedCredentials(secret, 60)
    token = credentials.issue_session(uuid4(), 1, now=NOW)
    value, signature = token.rsplit(".", 1)
    replacement = "A" if signature[0] != "A" else "B"
    assert credentials.read_session(value + "." + replacement + signature[1:], now=NOW) is None

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    alias = signature[:-1] + alphabet[alphabet.index(signature[-1]) + 1]
    assert base64_decode(alias) == base64_decode(signature)
    assert credentials.read_session(value + "." + alias, now=NOW) is None

    signer = URLSafeSerializer(
        secret.get_secret_value(),
        salt="scanpay-session-v1",
        signer_kwargs={"digest_method": sha256, "key_derivation": "hmac"},
    )
    incomplete = signer.dumps({"issued_at": int(NOW.timestamp())})
    assert credentials.read_session(incomplete, now=NOW) is None
    assert credentials.read_session("x" * 2049, now=NOW) is None


def test_csrf_requires_matching_signature_binding_and_expiry() -> None:
    credentials = SignedCredentials(SecretStr(token_urlsafe(32)), 60)
    token = credentials.issue_csrf("first session", now=NOW)
    assert credentials.valid_csrf(token, token, "first session", now=NOW)
    assert not credentials.valid_csrf(token, token, "second session", now=NOW)
    assert not credentials.valid_csrf(token, "", "first session", now=NOW)
    assert not credentials.valid_csrf(token, token + "x", "first session", now=NOW)
    assert not credentials.valid_csrf("bogus", "bogus", "first session", now=NOW)
    assert not credentials.valid_csrf(
        token, token, "first session", now=NOW + timedelta(minutes=10)
    )
    assert credentials.read_session(token, now=NOW) is None
    anonymous = credentials.issue_csrf(None, now=NOW)
    assert credentials.valid_csrf(anonymous, anonymous, None, now=NOW)
    assert not credentials.valid_csrf(anonymous, anonymous, "first session", now=NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_signing_secret": SecretStr("short")},
        {"auth_session_seconds": 0},
        {"auth_cookie_name": "not;valid"},
        {"trusted_origins": ("*",)},
        {"trusted_origins": ("https://example.test/path",)},
        {"app_mode": "demo", "auth_signing_secret": None},
        {"app_mode": "demo", "auth_cookie_secure": False},
        {"app_mode": "demo", "trusted_origins": ("http://example.test",)},
    ],
)
def test_invalid_auth_configuration_is_rejected(overrides: dict) -> None:
    options = {
        "app_mode": "local",
        "auth_signing_secret": SecretStr(token_urlsafe(32)),
        "trusted_origins": ("https://desk.example.test",),
        "database_url": None,
        "migration_database_url": None,
    }
    with pytest.raises(ValidationError):
        Settings(**(options | overrides))


def test_rate_limiter_reserves_attempts_and_expires_windows() -> None:
    current = [0.0]
    limiter = LoginRateLimiter(limit=2, seconds=10, max_entries=4, clock=lambda: current[0])
    assert limiter.retry_after("account-a", "peer-a") is None
    assert limiter.retry_after("account-a", "peer-b") is None
    assert limiter.retry_after("account-a", "peer-c") == 10
    assert limiter.retry_after("account-b", "peer-a") is None
    assert limiter.retry_after("account-c", "peer-a") == 10
    current[0] = 11.0
    assert limiter.retry_after("account-c", "peer-a") is None


def test_rate_limiter_caps_peer_username_rotation() -> None:
    limiter = LoginRateLimiter(limit=1, seconds=10, max_entries=32)
    for attempt in range(10):
        assert limiter.retry_after(f"account-{attempt}", "peer-a") is None
    assert limiter.retry_after("another-account", "peer-a") is not None
    assert limiter.retry_after("another-account", "peer-b") is None
