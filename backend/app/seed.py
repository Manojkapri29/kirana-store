"""Minimal development seed data: one shop and one owner user.

Shared reference data (units of measure) is NOT seeded here; it is inserted by the first migration so it
exists in every environment. This script only creates local development conveniences, and it creates no
products, sales or money: nothing that could be mistaken for real business data.

Usage (after `alembic upgrade head`):
    python -m app.seed

Safe to run repeatedly. Refuses to run when KIRANA_ENVIRONMENT=production.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import write_transaction
from app.models import Shop, User
from app.models.enums import UserRole

DEV_SHOP_NAME = "Development Shop"
DEV_USER_EMAIL = "owner@dev.kirana.local"
# Not a valid hash of any password, so nobody can log in as this user. Real credentials arrive in Phase 14.
UNUSABLE_PASSWORD_HASH = "!"


@dataclass(frozen=True)
class SeedResult:
    shop_id: int
    user_id: int
    created: bool


def seed_development_data(session: Session) -> SeedResult:
    """Create the development shop and owner if they do not exist yet. The caller owns the transaction."""
    created = False

    shop = session.scalar(select(Shop).where(Shop.name == DEV_SHOP_NAME))
    if shop is None:
        shop = Shop(name=DEV_SHOP_NAME, phone="0000000000", address="Local development only")
        session.add(shop)
        session.flush()
        created = True

    user = session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        user = User(
            shop_id=shop.id,
            email=DEV_USER_EMAIL,
            password_hash=UNUSABLE_PASSWORD_HASH,
            full_name="Development Owner",
            role=UserRole.OWNER,
        )
        session.add(user)
        session.flush()
        created = True

    return SeedResult(shop_id=shop.id, user_id=user.id, created=created)


def main(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.is_production:
        raise SystemExit("Refusing to seed development data when KIRANA_ENVIRONMENT=production.")

    with write_transaction() as session:
        result = seed_development_data(session)

    status = "created" if result.created else "already present"
    print(f"Development data {status}: shop_id={result.shop_id}, user_id={result.user_id}")


if __name__ == "__main__":
    main()
