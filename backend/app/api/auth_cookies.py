from datetime import UTC, datetime, timedelta

from fastapi import Response

from app.core.config import Settings


def cookie_name(settings: Settings, *, csrf: bool = False) -> str:
    prefix = "__Host-" if settings.secure_cookies else ""
    suffix = "_csrf" if csrf else ""
    return f"{prefix}{settings.auth_cookie_name}{suffix}"


def set_credential_cookie(
    response: Response, settings: Settings, value: str, seconds: int, *, csrf: bool = False
) -> None:
    response.set_cookie(
        key=cookie_name(settings, csrf=csrf),
        value=value,
        max_age=seconds,
        expires=datetime.now(UTC) + timedelta(seconds=seconds),
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )


def clear_credential_cookie(response: Response, settings: Settings, *, csrf: bool = False) -> None:
    response.delete_cookie(
        cookie_name(settings, csrf=csrf),
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
