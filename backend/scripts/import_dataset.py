import argparse
import sys
import warnings
from collections.abc import Sequence
from contextlib import ExitStack
from getpass import GetPassWarning, getpass
from json import dumps
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.database.session import Database
from app.models.values import BatchMode, FileType, UserRole
from app.schemas.auth import LoginRequest
from app.schemas.ingestion import ImportFailure, InputFile
from app.services.auth_service import AuthService, LoginThrottled
from app.services.csv_source import import_options
from app.services.ingestion_service import ingest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a local ScanPay CSV dataset.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--mode", choices=list(BatchMode), required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--assessment", action="store_true")
    for kind in FileType:
        parser.add_argument(f"--{kind.value.replace('_', '-')}", type=Path, dest=kind.value)
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        options = import_options(args.mode, args.cutoff, args.assessment)
    except (ValidationError, ImportFailure):
        print("Invalid configuration or import options.", file=sys.stderr)
        return 1
    if (
        settings.app_mode != "local"
        or settings.database_url is None
        or settings.auth_signing_secret is None
    ):
        print("Configure local mode, the database URL and authentication.", file=sys.stderr)
        return 1
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            credentials = LoginRequest(username=args.username, password=getpass("Password: "))
    except (GetPassWarning, EOFError, KeyboardInterrupt, ValidationError):
        print("A valid login and hidden password prompt are required.", file=sys.stderr)
        return 1

    database = Database(settings)
    try:
        with database.session() as session:
            actor = AuthService(settings).authenticate(session, credentials, "local-cli")
        if actor is None or actor.role != UserRole.ADMIN:
            print("Local administrator authentication required.", file=sys.stderr)
            return 1
        with ExitStack() as stack:
            files = {}
            for kind in FileType:
                path: Path | None = getattr(args, kind.value)
                if path is not None:
                    files[kind] = InputFile(path.name, stack.enter_context(path.open("rb")))
            batch, created = ingest(database, settings, actor, files, options)
        print(dumps({"batch_id": str(batch.batch_id), "status": batch.status, "created": created}))
        return 0
    except ImportFailure as exc:
        print(dumps(exc.details), file=sys.stderr)
    except (OSError, SQLAlchemyError, LoginThrottled):
        print("Import unavailable. Check database connectivity and input files.", file=sys.stderr)
    finally:
        database.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
