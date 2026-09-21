"""Small helpers that build valid rows. Tests override only the field they are testing."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Customer, Product, Shop, Supplier, Unit, User
from app.models.enums import UserRole

TODAY = date(2026, 9, 20)


def make_shop(session: Session, name: str = "Shop A", business_type: str = "GROCERY") -> Shop:
    shop = Shop(name=name, business_type=business_type, phone="9999999999", address="1 Test Street")
    session.add(shop)
    session.flush()
    return shop


def make_user(session: Session, shop: Shop, email: str | None = None) -> User:
    user = User(
        shop_id=shop.id,
        email=email or f"owner{shop.id}@test.local",
        password_hash="!",
        full_name="Test Owner",
        role=UserRole.OWNER,
    )
    session.add(user)
    session.flush()
    return user


def make_category(session: Session, shop: Shop, name: str = "Grocery") -> Category:
    category = Category(shop_id=shop.id, name=name)
    session.add(category)
    session.flush()
    return category


def make_supplier(session: Session, shop: Shop, name: str = "Sharma Traders") -> Supplier:
    supplier = Supplier(shop_id=shop.id, name=name, phone="9000000001")
    session.add(supplier)
    session.flush()
    return supplier


def make_customer(session: Session, shop: Shop, name: str = "Ramesh", phone: str | None = None) -> Customer:
    customer = Customer(shop_id=shop.id, name=name, phone=phone)
    session.add(customer)
    session.flush()
    return customer


def get_unit(session: Session, code: str = "pcs") -> Unit:
    return session.scalars(select(Unit).where(Unit.code == code)).one()


def product_kwargs(
    shop: Shop, category: Category, session: Session, **overrides: object
) -> dict[str, object]:
    fields: dict[str, object] = {
        "shop_id": shop.id,
        "sku": "SKU-1",
        "name": "Rice 5kg",
        "category_id": category.id,
        "unit_id": get_unit(session).id,
        "selling_price": Decimal("250.00"),
    }
    fields.update(overrides)
    return fields


def make_product(session: Session, shop: Shop, category: Category, **overrides: object) -> Product:
    product = Product(**product_kwargs(shop, category, session, **overrides))
    session.add(product)
    session.flush()
    return product


def today_in_shop_timezone() -> date:
    """Today as the app sees it (the shop's timezone, Asia/Kolkata), not the machine's local date."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


PASSWORD = "correct horse battery staple"


def make_login(
    session: Session,
    shop: Shop,
    role_code: str = "CASHIER",
    email: str | None = None,
    password: str = PASSWORD,
    full_name: str = "Test Person",
    status: str = "ACTIVE",
):  # noqa: ANN201
    """An account that can sign in, and its membership of `shop` with a system role. Returns (account, membership)."""
    from app.models import Account, Role
    from app.models.enums import MembershipStatus
    from app.services import password_service

    email = (email or f"{role_code.lower()}{shop.id}@test.local").lower()
    account = session.scalar(select(Account).where(Account.email == email))
    if account is None:
        account = Account(
            email=email, full_name=full_name, password_hash=password_service.hash_password(password)
        )
        session.add(account)
        session.flush()
    role = session.scalar(select(Role).where(Role.shop_id.is_(None), Role.code == role_code))
    member = User(
        shop_id=shop.id, email=email, password_hash="!", full_name=full_name,
        role=UserRole.OWNER if role_code == "OWNER" else UserRole.STAFF,
        account_id=account.id, role_id=role.id, status=MembershipStatus(status), is_active=status == "ACTIVE",
    )  # fmt: skip
    session.add(member)
    session.flush()
    return account, member


def link_owner(session: Session, user: User, password: str = PASSWORD):  # noqa: ANN201
    """Give an existing test owner (made by `make_user`) a sign-in account and the OWNER role."""
    from app.models import Account, Role
    from app.models.enums import MembershipStatus
    from app.services import password_service

    account = Account(
        email=user.email, full_name=user.full_name, password_hash=password_service.hash_password(password)
    )
    session.add(account)
    session.flush()
    user.account_id = account.id
    user.role_id = session.scalar(select(Role.id).where(Role.shop_id.is_(None), Role.code == "OWNER"))
    user.status = MembershipStatus.ACTIVE
    session.flush()
    return account
