"""Sales reports: Detailed, Quick and Combined; Gross, Discount and Net; where discounts went.

Read-only. Only posted sales count. The basic summary is open to every plan; the discount analytics need the
plan's advanced reports. A Quick Sale has no cost, so no report here ever shows profit for it.
"""

from datetime import date

from fastapi import APIRouter

from app.api.deps import Ctx
from app.db.session import read_session
from app.schemas.report import DiscountReportOut, SalesSummaryOut
from app.services import sales_report_service

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/sales-summary", response_model=SalesSummaryOut)
def sales_summary(ctx: Ctx, date_from: date | None = None, date_to: date | None = None) -> SalesSummaryOut:
    """Gross sales, discount and net sales for Detailed, Quick and Combined sales, with a row per day.
    Defaults to the month so far."""
    with read_session() as session:
        return SalesSummaryOut.from_summary(
            sales_report_service.sales_summary(session, ctx.shop_id, date_from, date_to)
        )


@router.get("/discounts", response_model=DiscountReportOut)
def discounts(ctx: Ctx, date_from: date | None = None, date_to: date | None = None) -> DiscountReportOut:
    """Total discount, promotions and coupons used, and discount by promotion, coupon and day. Needs the
    plan's advanced reports."""
    with read_session() as session:
        return DiscountReportOut.from_report(
            sales_report_service.discount_report(session, ctx.shop_id, date_from, date_to)
        )
