from http import HTTPStatus

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.core.logging import logger


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    try:
        status = HTTPStatus(exc.status_code)
    except ValueError:
        return error_response(exc.status_code, "http_error", "Request failed", exc.headers)

    return error_response(status.value, status.name.lower(), status.phrase, exc.headers)


async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "validation_error", "Invalid request")


async def unexpected_errors(request: Request, call_next: RequestResponseEndpoint) -> Response:
    try:
        return await call_next(request)
    except Exception as exc:
        logger.error("Unhandled request error (%s)", type(exc).__name__)
        return error_response(500, "internal_server_error", "Internal server error")
