from http import HTTPStatus

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.core.logging import logger
from app.schemas.ingestion import ImportFailure


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


async def import_error(_request: Request, exc: ImportFailure) -> JSONResponse:
    status = HTTPStatus(exc.status)
    return JSONResponse(
        status_code=status.value,
        content={
            "error": {
                "code": "validation_error"
                if status == HTTPStatus.UNPROCESSABLE_ENTITY
                else status.name.lower(),
                "message": status.phrase,
                "details": exc.details,
            }
        },
    )


async def unexpected_errors(request: Request, call_next: RequestResponseEndpoint) -> Response:
    try:
        return await call_next(request)
    except (OperationalError, PoolTimeout) as exc:
        logger.warning("Database unavailable (%s)", type(exc).__name__)
        return error_response(503, "service_unavailable", "Service Unavailable")
    except Exception as exc:
        logger.error("Unhandled request error (%s)", type(exc).__name__)
        return error_response(500, "internal_server_error", "Internal server error")
