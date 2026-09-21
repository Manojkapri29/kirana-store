"""Operator tool for sign-in accounts and first shops (there is no public sign-up, and no email is sent).

python -m app.account_cli create-shop --shop-name "Sharma Store" --owner-email owner@example.com --owner-name "R Sharma"
python -m app.account_cli set-password --email owner@example.com        # a new temporary password, shown once
python -m app.account_cli unlock --email owner@example.com              # end a sign-in pause
python -m app.account_cli disable --email someone@example.com           # the person can no longer sign in anywhere
python -m app.account_cli enable --email someone@example.com
python -m app.account_cli list

Passwords are generated here and shown ONCE: they are stored only as Argon2id hashes, so they cannot be shown again. Give the
password to the person over a channel you trust and ask them to change it after signing in. `set-password` ends all of the
person's sessions. Nothing here reads or prints an existing password or token.
"""

import argparse
import secrets

from sqlalchemy import select

from app.db.session import read_session, write_transaction
from app.models import Account, Role, Shop, User
from app.models.enums import LoginStatus, MembershipStatus, UserRole
from app.services import auth_service, password_service


def _account(session, email: str) -> Account:  # noqa: ANN001
    account = session.scalar(select(Account).where(Account.email == auth_service.normalize_email(email)))
    if account is None:
        raise SystemExit(f"No account for {email}.")
    return account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="account_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-shop")
    create.add_argument("--shop-name", required=True)
    create.add_argument("--owner-email", required=True)
    create.add_argument("--owner-name", required=True)
    create.add_argument(
        "--business-type",
        default="GROCERY",
        help="the kind of business (a code from the business types list)",
    )
    create.add_argument("--phone", default="Not provided")
    create.add_argument("--address", default="Not provided")
    for name in ("set-password", "unlock", "disable", "enable"):
        sub.add_parser(name).add_argument("--email", required=True)
    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.command == "list":
        with read_session() as session:
            for a in session.scalars(select(Account).order_by(Account.email)):
                shops = len(auth_service.memberships_of(session, a.id))
                print(f"{a.email:<40} {a.status.value:<9} shops={shops} {'PAUSED' if a.locked_until else ''}")
        return 0

    with write_transaction() as session:
        if args.command == "create-shop":
            email = auth_service.normalize_email(args.owner_email)
            if session.scalar(select(Account.id).where(Account.email == email)) is not None:
                raise SystemExit(
                    "An account with that email exists. Invite it to the shop from the Staff screen instead."
                )
            password = secrets.token_urlsafe(12)
            shop = Shop(
                name=args.shop_name.strip(),
                business_type=args.business_type,
                phone=args.phone,
                address=args.address,
            )
            session.add(shop)
            session.flush()
            account = Account(
                email=email,
                full_name=args.owner_name.strip(),
                password_hash=password_service.hash_password(password),
            )
            session.add(account)
            session.flush()
            owner_role = session.scalar(select(Role).where(Role.shop_id.is_(None), Role.code == "OWNER"))
            session.add(
                User(
                    shop_id=shop.id,
                    email=email,
                    full_name=account.full_name,
                    password_hash=password_service.UNUSABLE,
                    role=UserRole.OWNER,
                    account_id=account.id,
                    role_id=owner_role.id,
                    status=MembershipStatus.ACTIVE,
                )  # fmt: skip
            )
            print(f"Created shop '{shop.name}' (id {shop.id}) and its owner {email}.")
            print(f"Temporary password (shown once): {password}")
            return 0
        account = _account(session, args.email)
        if args.command == "set-password":
            password = secrets.token_urlsafe(12)
            account.password_hash = password_service.hash_password(password)
            account.failed_logins, account.locked_until = 0, None
            ended = auth_service.revoke_all_for_account(
                session, account.id, except_session_id=None, reason="password_reset"
            )
            print(f"New temporary password for {account.email} (shown once): {password}")
            print(f"{ended} session(s) ended.")
        elif args.command == "unlock":
            account.failed_logins, account.locked_until = 0, None
            print("Unlocked.")
        elif args.command == "disable":
            account.status = LoginStatus.DISABLED
            auth_service.revoke_all_for_account(
                session, account.id, except_session_id=None, reason="disabled"
            )
            print("Disabled. Their sessions have ended.")
        elif args.command == "enable":
            account.status = LoginStatus.ACTIVE
            print("Enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
