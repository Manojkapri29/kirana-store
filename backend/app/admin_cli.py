"""Operator tool for system administrators (not shop users).

python -m app.admin_cli create --email ops@example.com --name "Ops" --role OPERATIONS_ADMIN
python -m app.admin_cli rotate --email ops@example.com
python -m app.admin_cli deactivate --email ops@example.com
python -m app.admin_cli list

`create` and `rotate` print the token ONCE. It is stored only as a hash, so it cannot be shown again: keep it in a password
manager. Send it as the `X-Admin-Token` header. Roles: SUPER_ADMIN, OPERATIONS_ADMIN, SUPPORT_ADMIN (see docs/SAAS_ADMIN.md).
"""

import argparse

from sqlalchemy import select

from app.db.session import read_session, write_transaction
from app.models import SystemAdmin
from app.models.enums import AdminRole
from app.services import admin_service
from app.services.errors import DomainError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="admin_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="")
    create.add_argument("--role", required=True, choices=[r.value for r in AdminRole])
    for name in ("rotate", "deactivate"):
        sub.add_parser(name).add_argument("--email", required=True)
    sub.add_parser("list")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            with write_transaction() as session:
                admin, token = admin_service.create_admin(
                    session, email=args.email, display_name=args.name, role=AdminRole(args.role)
                )
            print(f"Created {admin.email} ({admin.role.value}).\nToken (shown once, keep it safe):\n{token}")
        elif args.command == "rotate":
            with write_transaction() as session:
                token = admin_service.rotate_token(session, args.email)
            print(f"New token for {args.email} (the old one no longer works; shown once):\n{token}")
        elif args.command == "deactivate":
            with write_transaction() as session:
                admin_service.deactivate(session, args.email)
            print(f"Deactivated {args.email}.")
        else:
            with read_session() as session:
                for admin in session.scalars(select(SystemAdmin).order_by(SystemAdmin.id)):
                    print(
                        f"{admin.email}  {admin.role.value}  {'active' if admin.is_active else 'inactive'}  {admin.token_prefix}..."
                    )
    except DomainError as error:
        print(f"Error: {error.message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
