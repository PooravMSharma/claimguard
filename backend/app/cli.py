"""Admin commands, run inside the backend container.

Create the first admin (asks for the password, never shows it):
    docker compose exec backend python -m app.cli create-admin --email you@company.in --name "Your Name"
"""

import argparse
import getpass
import sys

from app.db.session import SessionLocal
from app.models.enums import RoleName
from app.services.audit import log_event
from app.services.users import UserError, create_user


def _ask_password() -> str:
    first = getpass.getpass("Password (min 10 characters, letters + numbers/symbols): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    return first


def create_admin(email: str, name: str, password: str) -> None:
    with SessionLocal() as db:
        user = create_user(db, email=email, full_name=name, role=RoleName.ADMIN, password=password)
        log_event(db, "user.created", entity_type="user", entity_id=user.id,
                  details={"email": user.email, "role": "admin", "via": "cli"})
        db.commit()
        print(f"Admin created: {user.email}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="Create an admin account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--password-stdin", action="store_true", help="Read the password from standard input")
    args = parser.parse_args(argv)

    if args.command == "create-admin":
        password = sys.stdin.readline().rstrip("\n") if args.password_stdin else _ask_password()
        try:
            create_admin(args.email, args.name, password)
        except UserError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()