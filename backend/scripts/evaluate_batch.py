import argparse
import sys
import warnings
from collections.abc import Sequence
from getpass import GetPassWarning, getpass
from json import dumps
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.database.session import Database
from app.models.values import UserRole
from app.schemas.auth import LoginRequest
from app.schemas.evaluation import EvaluationError
from app.services.auth_service import AuthService, LoginThrottled
from app.services.evaluate_batch import evaluate_batch


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate an imported local ScanPay batch.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--batch-id", required=True, type=UUID)
    args = parser.parse_args(argv)
    try:
        settings = Settings()
        if (
            settings.app_mode != "local"
            or settings.database_url is None
            or settings.auth_signing_secret is None
        ):
            print("Configure local mode, the database URL and authentication.", file=sys.stderr)
            return 1
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            credentials = LoginRequest(username=args.username, password=getpass("Password: "))
    except (GetPassWarning, EOFError, KeyboardInterrupt, ValidationError):
        print(
            "Valid local settings, credentials and a hidden password prompt are required.",
            file=sys.stderr,
        )
        return 1

    database = Database(settings)
    try:
        with database.session() as session:
            actor = AuthService(settings).authenticate(session, credentials, "local-cli")
        if actor is None or actor.role != UserRole.ADMIN:
            print("Local administrator authentication required.", file=sys.stderr)
            return 1
        result = evaluate_batch(database, actor, args.batch_id)
        print(
            dumps(
                {
                    "run_id": str(result.run.run_id),
                    "batch_id": str(result.run.batch_id),
                    "status": result.run.status,
                    "created": result.created,
                }
            )
        )
        return 0
    except EvaluationError as exc:
        print(dumps({"error": exc.code, "status": exc.status}), file=sys.stderr)
    except (SQLAlchemyError, LoginThrottled):
        print(
            "Evaluation unavailable. Check database connectivity and authentication.",
            file=sys.stderr,
        )
    finally:
        database.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
