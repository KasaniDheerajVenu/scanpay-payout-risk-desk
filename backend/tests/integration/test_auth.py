from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from app.api.auth_cookies import cookie_name
from app.api.dependencies import (
    get_current_user,
    get_review_scope,
    require_admin,
    require_analyst_or_admin,
    require_permission,
)
from app.core.config import AppMode, Settings
from app.core.permissions import Permission
from app.core.review_scope import ActorContext
from app.core.security import hash_password, verify_password
from app.main import create_app
from app.models.user import User
from scripts import create_user

PASSWORD = "fictional integration password"
ORIGIN = "http://127.0.0.1:5173"
DEMO_ORIGIN = "https://desk.example.test"
UNAUTHORIZED = {"error": {"code": "unauthorized", "message": "Unauthorized"}}


@pytest.fixture
def identities(engine: Engine) -> Iterator[dict[str, UUID]]:
    password_hash = hash_password(PASSWORD)
    accounts = [
        ("fixture-admin", "ADMIN", True),
        ("fixture-analyst", "ANALYST", True),
        ("fixture-viewer", "VIEWER", True),
        ("fixture-inactive", "ANALYST", False),
    ]
    with engine.begin() as connection:
        identities = {
            username: connection.scalar(
                insert(User)
                .values(
                    username=username,
                    display_name=f"Synthetic {role}",
                    role=role,
                    password_hash=password_hash,
                    is_active=is_active,
                )
                .returning(User.user_id)
            )
            for username, role, is_active in accounts
        }
    try:
        yield identities
    finally:
        with engine.begin() as connection:
            connection.execute(delete(User).where(User.user_id.in_(identities.values())))


def make_settings(url: SecretStr, mode: AppMode = "local", *, limit: int = 5) -> Settings:
    return Settings(
        database_url=url,
        migration_database_url=None,
        app_mode=mode,
        auth_signing_secret=SecretStr(token_urlsafe(48)),
        trusted_origins=(DEMO_ORIGIN if mode == "demo" else ORIGIN,),
        login_rate_limit=limit,
    )


def add_test_routes(app: FastAPI) -> None:
    @app.get("/api/test/default")
    def default_protected() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/test/actor")
    def actor(
        user: Annotated[ActorContext, Depends(get_current_user)],
        scope: Annotated[str, Depends(get_review_scope)],
    ) -> dict[str, str]:
        return {"actor": str(user.user_id), "scope": scope}

    @app.get("/api/test/analyst")
    def analyst(_user: Annotated[ActorContext, Depends(require_analyst_or_admin)]) -> None:
        return None

    @app.get("/api/test/admin")
    def admin(_user: Annotated[ActorContext, Depends(require_admin)]) -> None:
        return None

    @app.get("/api/test/upload")
    def upload(
        _user: Annotated[ActorContext, Depends(require_permission(Permission.UPLOAD_DATASETS))],
    ) -> None:
        return None

    @app.get("/api/test/synthetic")
    def synthetic(
        _user: Annotated[
            ActorContext, Depends(require_permission(Permission.IMPORT_SYNTHETIC_DEMO))
        ],
    ) -> None:
        return None


@pytest.fixture
def client(test_database_url: SecretStr, identities: dict[str, UUID]) -> Iterator[TestClient]:
    app = create_app(make_settings(test_database_url))
    add_test_routes(app)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def csrf_headers(client: TestClient, origin: str = ORIGIN) -> dict[str, str]:
    response = client.get("/api/auth/csrf")
    assert response.status_code == 200
    return {"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]}


def login(client: TestClient, username: str, password: str = PASSWORD, *, origin: str = ORIGIN):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=csrf_headers(client, origin),
    )


def test_login_sets_cookie_and_returns_only_safe_identity(
    client: TestClient, identities: dict[str, UUID]
) -> None:
    response = login(client, " FIXTURE-ANALYST ")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    cookie = response.headers.get_list("set-cookie")[0].lower()
    assert cookie.startswith("scanpay_session=")
    assert all(
        option in cookie
        for option in ("httponly", "samesite=lax", "path=/", "max-age=", "expires=")
    )
    assert "domain=" not in cookie
    assert "secure" not in cookie
    assert response.json() == {
        "user_id": str(identities["fixture-analyst"]),
        "username": "fixture-analyst",
        "display_name": "Synthetic ANALYST",
        "role": "ANALYST",
        "mode": "local",
        "permissions": ["export_decisions", "override_decisions", "read_risk", "view_audit"],
    }
    assert client.get("/api/auth/me").json() == response.json()


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("fixture-analyst", "fictional wrong password"),
        ("fixture-unknown", PASSWORD),
        ("fixture-inactive", PASSWORD),
    ],
)
def test_failed_login_has_the_same_public_response(
    client: TestClient, username: str, password: str
) -> None:
    response = login(client, username, password)
    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED
    assert client.cookies.get("scanpay_session") is None


def test_authentication_is_default_for_api_routes_and_health_stays_public(
    client: TestClient,
) -> None:
    for route in ("/api/auth/me", "/api/test/default", "/api/test/admin"):
        assert client.get(route).status_code == 401
    assert client.post("/api/auth/logout").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 404


def test_tampered_expired_and_unknown_account_sessions_are_rejected(
    client: TestClient, identities: dict[str, UUID]
) -> None:
    auth = client.app.state.auth
    token = auth.credentials.issue_session(identities["fixture-analyst"], 1)
    body, signature = token.rsplit(".", 1)
    forged = body + "." + ("B" if signature[0] == "A" else "A") + signature[1:]
    expired = auth.credentials.issue_session(
        identities["fixture-analyst"], 1, now=datetime.now(UTC) - timedelta(hours=2)
    )
    unknown = auth.credentials.issue_session(UUID(int=0), 1)
    for cookie in (forged, expired, unknown):
        client.cookies.set("scanpay_session", cookie)
        assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("change", [{"is_active": False}, {"auth_version": 2}])
def test_account_state_changes_invalidate_sessions(
    client: TestClient, engine: Engine, identities: dict[str, UUID], change: dict
) -> None:
    assert login(client, "fixture-analyst").status_code == 200
    with engine.begin() as connection:
        connection.execute(
            update(User).where(User.user_id == identities["fixture-analyst"]).values(**change)
        )
    assert client.get("/api/auth/me").status_code == 401


def test_role_change_is_reflected_immediately(
    client: TestClient, engine: Engine, identities: dict[str, UUID]
) -> None:
    assert login(client, "fixture-analyst").status_code == 200
    assert client.get("/api/test/analyst").status_code == 200
    with engine.begin() as connection:
        connection.execute(
            update(User).where(User.user_id == identities["fixture-analyst"]).values(role="VIEWER")
        )
    assert client.get("/api/test/analyst").status_code == 403
    assert client.get("/api/auth/me").json()["role"] == "VIEWER"


@pytest.mark.parametrize(
    ("account", "analyst", "admin"),
    [("fixture-viewer", 403, 403), ("fixture-analyst", 200, 403), ("fixture-admin", 200, 200)],
)
def test_role_guards_are_enforced(
    client: TestClient, account: str, analyst: int, admin: int
) -> None:
    assert login(client, account).status_code == 200
    assert client.get("/api/test/default").status_code == 200
    assert client.get("/api/test/analyst").status_code == analyst
    assert client.get("/api/test/admin").status_code == admin
    assert client.get("/api/test/upload").status_code == admin
    assert client.get("/api/test/synthetic").status_code == 403


def test_logout_clears_cookies_and_revokes_all_account_sessions(
    client: TestClient, engine: Engine, identities: dict[str, UUID]
) -> None:
    assert login(client, "fixture-analyst").status_code == 200
    first_cookie = client.cookies.get("scanpay_session")
    assert login(client, "fixture-analyst").status_code == 200
    second_cookie = client.cookies.get("scanpay_session")
    assert first_cookie != second_cookie
    response = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 204 and not response.content
    assert any(
        "scanpay_session=" in cookie and "Max-Age=0" in cookie
        for cookie in response.headers.get_list("set-cookie")
    )
    assert client.cookies.get("scanpay_session") is None
    assert client.cookies.get("scanpay_session_csrf") is None
    for old_cookie in (first_cookie, second_cookie):
        client.cookies.set("scanpay_session", old_cookie)
        assert client.get("/api/auth/me").status_code == 401
    with engine.connect() as conn:
        assert (
            conn.scalar(
                select(User.auth_version).where(User.user_id == identities["fixture-analyst"])
            )
            == 2
        )


def test_csrf_and_origin_checks_protect_login_and_authenticated_writes(client: TestClient) -> None:
    credentials = {"username": "fixture-analyst", "password": PASSWORD}
    valid = csrf_headers(client)
    assert client.post("/api/auth/login", json=credentials).status_code == 403
    for incorrect in (
        {"Origin": ORIGIN},
        {"X-CSRF-Token": valid["X-CSRF-Token"]},
        valid | {"X-CSRF-Token": "invalid"},
        valid | {"Origin": "https://untrusted.example.test"},
    ):
        assert (
            client.post("/api/auth/login", json=credentials, headers=incorrect).status_code == 403
        )
    assert (
        client.get(
            "/api/auth/csrf", headers={"Origin": "https://untrusted.example.test"}
        ).status_code
        == 403
    )
    assert client.post("/api/auth/login", json=credentials, headers=valid).status_code == 200
    assert client.post("/api/auth/logout", headers=valid).status_code == 403
    headers = csrf_headers(client)
    assert client.post("/api/test/actor").status_code == 403
    assert (
        client.post(
            "/api/test/actor",
            headers=headers
            | {"Origin": "https://untrusted.example.test", "X-Forwarded-Host": "127.0.0.1:5173"},
        ).status_code
        == 403
    )
    assert client.get("/api/auth/me").status_code == 200


def test_login_limits_body_and_rejects_untrusted_identity_fields(client: TestClient) -> None:
    headers = csrf_headers(client)
    credentials = {"username": "fixture-admin", "password": PASSWORD}
    assert (
        client.post(
            "/api/auth/login",
            json=credentials | {"role": "ADMIN", "scope_key": "forged"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/auth/login", json=credentials | {"password": "x" * 129}, headers=headers
        ).status_code
        == 422
    )
    response = client.post(
        "/api/auth/login",
        content="x" * 4097,
        headers=headers | {"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_login_abuse_returns_retry_after(
    test_database_url: SecretStr, identities: dict[str, UUID]
) -> None:
    app = create_app(make_settings(test_database_url, limit=2))
    with TestClient(app, base_url=ORIGIN) as client:
        for _ in range(2):
            assert login(client, "fixture-analyst", "wrong").status_code == 401
        response = login(client, "fixture-analyst")
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
        assert login(client, "fixture-admin").status_code == 200


@pytest.mark.parametrize("mode", ["local", "demo"])
def test_actor_and_scope_come_from_the_verified_session(
    test_database_url: SecretStr, identities: dict[str, UUID], mode: AppMode
) -> None:
    settings = make_settings(test_database_url, mode)
    app = create_app(settings)
    add_test_routes(app)
    origin = DEMO_ORIGIN if mode == "demo" else ORIGIN
    scopes = []
    for username in ("fixture-admin", "fixture-analyst"):
        with TestClient(app, base_url=origin) as client:
            response = login(client, username, origin=origin)
            assert response.status_code == 200
            if mode == "demo":
                cookie = response.headers.get_list("set-cookie")[0].lower()
                assert cookie.startswith("__host-scanpay_session=") and "; secure" in cookie
                assert "domain=" not in cookie
                assert client.cookies.get(cookie_name(settings)) is not None
                assert client.get("/api/test/upload").status_code == 403
                synthetic_allowed = 200 if username == "fixture-admin" else 403
                assert client.get("/api/test/synthetic").status_code == synthetic_allowed
            result = client.post(
                "/api/test/actor",
                params={"changed_by": str(UUID(int=1)), "scope_key": "forged"},
                headers=csrf_headers(client, origin)
                | {"X-Scope-Key": "forged", "X-User-Id": str(UUID(int=1))},
            )
            assert result.status_code == 200
            assert result.json()["actor"] == str(identities[username])
            scopes.append(result.json()["scope"])
    if mode == "demo":
        assert scopes == [
            f"demo:{identities['fixture-admin']}",
            f"demo:{identities['fixture-analyst']}",
        ]
    else:
        assert scopes == ["local", "local"]


def test_provisioning_cli_hashes_password_and_rejects_duplicate(
    engine: Engine, test_database_url: SecretStr, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("SCANPAY_DATABASE_URL", test_database_url.get_secret_value())
    monkeypatch.setenv("SCANPAY_APP_MODE", "local")
    monkeypatch.setattr(create_user, "getpass", lambda _prompt: PASSWORD)
    username = "fictional-provisioning-test"
    arguments = ["--username", username, "--display-name", "Demo Analyst", "--role", "ANALYST"]
    try:
        assert create_user.main(arguments) == 0
        assert create_user.main(arguments) == 1
        with engine.connect() as connection:
            row = connection.execute(
                select(User.password_hash, User.role).where(User.username == username)
            ).one()
            assert row.role == "ANALYST" and verify_password(PASSWORD, row.password_hash)
            assert row.password_hash.startswith("$argon2id$")
        output = capsys.readouterr()
        assert PASSWORD not in output.out + output.err
        assert "already provisioned" in output.err
    finally:
        with engine.begin() as connection:
            connection.execute(delete(User).where(User.username == username))
