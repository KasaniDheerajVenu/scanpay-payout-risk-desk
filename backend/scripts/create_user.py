import argparse
import sys
import warnings
from collections.abc import Sequence
from getpass import GetPassWarning, getpass

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.security import hash_password
from app.database.session import Database
from app.models.values import UserRole
from app.repositories.users import insert_user
from app.schemas.auth import UserProvision


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision a Payout Risk Desk application user.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--role", choices=list(UserRole), required=True)
    args = parser.parse_args(argv)

    try:
        user = UserProvision(username=args.username, display_name=args.display_name, role=args.role)
        settings = Settings()
    except ValidationError:
        print("Invalid user details or application configuration.", file=sys.stderr)
        return 1
    if settings.database_url is None:
        print("Configure SCANPAY_DATABASE_URL first.", file=sys.stderr)
        return 1

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("Password: ")
            confirmation = getpass("Confirm password: ")
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        print("A hidden, interactive password prompt is required.", file=sys.stderr)
        return 1

    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    database = Database(settings)
    try:
        with database.session() as session:
            user_id = insert_user(
                session, user.username, user.display_name, user.role, password_hash
            )
            if user_id is None:
                print("That username is already provisioned.", file=sys.stderr)
                return 1
            session.commit()
    except SQLAlchemyError:
        print(
            "Could not provision the user. Check database access and migrations.", file=sys.stderr
        )
        return 1
    finally:
        database.close()

    print("Application user created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
