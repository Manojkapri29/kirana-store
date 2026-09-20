"""Shop-level lookups and settings."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Shop
from app.services.errors import NotFoundError


def get_shop(session: Session, shop_id: int) -> Shop:
    shop = session.scalar(select(Shop).where(Shop.id == shop_id))
    if shop is None:
        raise NotFoundError("Shop not found")
    return shop


def shop_today(shop: Shop) -> date:
    """Today's date in the shop's own timezone (business dates are shop-local)."""
    return datetime.now(ZoneInfo(shop.timezone)).date()
