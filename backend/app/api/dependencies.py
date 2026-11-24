from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.database.session import Database


def get_database(request: Request) -> Database | None:
    return request.app.state.database


def get_session(request: Request) -> Iterator[Session]:
    database = get_database(request)
    if database is None:
        raise HTTPException(status_code=503)
    with database.session() as session:
        yield session
