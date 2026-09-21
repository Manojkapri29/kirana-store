"""Operator tool for plans and shop subscriptions. There is no payment step and no HTTP endpoint for this.

python -m app.subscription_admin plans
python -m app.subscription_admin assign --shop 1 --plan pro [--trial] [--days 30]
python -m app.subscription_admin show --shop 1
python -m app.subscription_admin set-limit --plan basic --key max_products --value 500  (none = unlimited)
python -m app.subscription_admin set-feature --plan basic --key promotions --on|--off
"""

import argparse
from datetime import timedelta

from app.core.config import get_settings
from app.db.session import read_session, write_transaction
from app.db.types import utc_now
from app.models.enums import SubscriptionStatus
from app.services import entitlement_service as plans


def _show(shop_id: int) -> None:
    with read_session() as session:
        e = plans.get_entitlements(session, shop_id)
    print(f"shop {shop_id}: plan={e.plan_code} ({e.source}) status={e.status} ends={e.ends_at}")
    print("  features:", {k: v for k, v in sorted(e.features.items())})
    print("  limits:  ", {k: v for k, v in sorted(e.limits.items())})


def main(argv: list[str] | None = None) -> None:
    if get_settings().is_production:
        raise SystemExit(
            "Refusing to run against production. Set KIRANA_ENVIRONMENT=development to run it deliberately."
        )
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plans")
    assign = sub.add_parser("assign")
    assign.add_argument("--shop", type=int, required=True)
    assign.add_argument("--plan", required=True)
    assign.add_argument("--trial", action="store_true")
    assign.add_argument("--days", type=int)
    show = sub.add_parser("show")
    show.add_argument("--shop", type=int, required=True)
    limit = sub.add_parser("set-limit")
    limit.add_argument("--plan", required=True)
    limit.add_argument("--key", required=True)
    limit.add_argument("--value", required=True)
    feature = sub.add_parser("set-feature")
    feature.add_argument("--plan", required=True)
    feature.add_argument("--key", required=True)
    on_off = feature.add_mutually_exclusive_group(required=True)
    on_off.add_argument("--on", action="store_true")
    on_off.add_argument("--off", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "plans":
        with read_session() as session:
            for view in plans.list_plans(session, include_inactive=True):
                print(
                    f"{view.plan.code}: {view.plan.name} price={view.plan.price} {view.plan.currency} "
                    f"active={view.plan.is_active}"
                )
                print("   features:", view.features, " limits:", view.limits)
    elif args.command == "assign":
        ends = utc_now() + timedelta(days=args.days) if args.days else None
        status = SubscriptionStatus.TRIAL if args.trial else SubscriptionStatus.ACTIVE
        with write_transaction() as session:
            plans.assign_plan(session, args.shop, args.plan, status=status, ends_at=ends)
        _show(args.shop)
    elif args.command == "show":
        _show(args.shop)
    elif args.command == "set-limit":
        value = None if args.value.lower() == "none" else int(args.value)
        with write_transaction() as session:
            plans.set_plan_entries(session, args.plan, limits={args.key: value})
        print(f"{args.plan}: {args.key} = {'unlimited' if value is None else value}")
    elif args.command == "set-feature":
        with write_transaction() as session:
            plans.set_plan_entries(session, args.plan, features={args.key: args.on})
        print(f"{args.plan}: {args.key} = {'on' if args.on else 'off'}")


if __name__ == "__main__":
    main()
