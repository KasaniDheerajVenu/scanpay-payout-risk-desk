import argparse
import sys
import warnings
from collections.abc import Sequence
from getpass import GetPassWarning, getpass
from json import dumps
from pathlib import Path
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.database.session import Database
from app.models.values import UserRole
from app.risk_engine.fraud_evidence import CaseMapping, MappedCase, mapping_index
from app.schemas.auth import LoginRequest
from app.schemas.evaluation import EvaluationError
from app.schemas.fraud_findings import NodeKind
from app.services.auth_service import AuthService, LoginThrottled
from app.services.evaluate_batch import evaluate_batch


class ReviewedCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed_payout_id: str
    transaction_ids: tuple[str, ...]
    available_at: AwareDatetime
    implicated_profile_kinds: tuple[NodeKind, ...] = ()


class ReviewedMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    cases: tuple[ReviewedCase, ...] = Field(min_length=1)


def load_mapping(path: Path) -> CaseMapping:
    limit = 256 * 1024
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("invalid_fraud_case_mapping")
    parsed = ReviewedMapping.model_validate_json(raw)
    mapping = CaseMapping(
        parsed.version,
        tuple(MappedCase(**case.model_dump()) for case in parsed.cases),
    )
    mapping_index(mapping)
    return mapping


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate an imported local ScanPay batch.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--batch-id", required=True, type=UUID)
    parser.add_argument("--case-mapping", type=Path, help="Reviewed private case mapping JSON")
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
        try:
            mapping = load_mapping(args.case_mapping) if args.case_mapping else None
        except (OSError, ValueError):
            print("Invalid or unavailable reviewed case mapping.", file=sys.stderr)
            return 1
        result = evaluate_batch(database, actor, args.batch_id, mapping=mapping)
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
