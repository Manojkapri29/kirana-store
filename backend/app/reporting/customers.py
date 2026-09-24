"""Customer and CRM analytics, built on the Phase 14 CRM data and the purchase history that already exists.

Only IDENTIFIED purchases (a posted sale or quick sale that names a customer) can be attributed to a customer; a walk-in
sale belongs to nobody and is never invented into a customer. Everything reported is a factual count or sum of the shop's
own records. Nothing here infers a sensitive attribute, a personality or a protected characteristic; segments are the
CRM's own factual buckets (recency, frequency, spend, credit).

Online customer activity counts the shop's own online orders; their revenue is already in the detailed sales above.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignSend, LoyaltyLedger, ReferralEvent
from app.reporting import facts, online
from app.reporting.filters import ReportFilters, ReportTable
from app.reporting.sales import _table
from app.services import customer_intelligence_service as cis
from app.services import retention_service

ZERO = Decimal("0.00")
_HONOURED = {"customer_id", "active"}
ONLINE_NA = "Online orders are ordinary detailed sales once delivered, so their revenue is already inside the figures above."


def _pct(n: int, d: int) -> Decimal | None:
    return (Decimal(n) / Decimal(d) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN) if d else None


def _per_customer(session: Session, shop_id: int, f: ReportFilters) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for cid, day, amount, kind in facts.identified_purchases(session, shop_id, f.period.start, f.period.end):
        if f.customer_id is not None and cid != f.customer_id:
            continue
        g = out.setdefault(cid, {"purchases": 0, "revenue": ZERO, "detailed": 0, "quick": 0, "last": day})
        g["purchases"] += 1
        g["revenue"] += amount
        g["detailed" if kind == "DETAILED" else "quick"] += 1
        g["last"] = max(g["last"], day)
    return out


def overview(session: Session, shop_id: int, f: ReportFilters, today: date) -> dict:
    per = _per_customer(session, shop_id, f)
    first = facts.first_purchase_dates(session, shop_id)
    new = sum(1 for c in per if f.period.contains(first[c]))
    returning = sum(1 for c in per if first[c] < f.period.start)
    revenue = sum((g["revenue"] for g in per.values()), ZERO)
    purchases = sum(g["purchases"] for g in per.values())
    r = retention_service.retention_summary(session, shop_id, today)
    return {
        "period": f.period,
        "comparison": f.comparison,
        "purchasing_customers": len(per),
        "new_customers": new,
        "returning_customers": returning,
        "revenue_from_identified_customers": revenue,
        "average_customer_value": (revenue / len(per)).quantize(Decimal("0.01")) if per else None,
        "purchase_frequency": (Decimal(purchases) / len(per)).quantize(Decimal("0.01")) if per else None,
        "repeat_purchase_rate_pct": r.repeat_purchase_rate,
        "inactive_customers": r.inactive_customer_count,
        "reactivated_customers": r.reactivated_count,
        "online_customer_activity": online.period_summary(session, shop_id, f.period.start, f.period.end),
        "online_note": ONLINE_NA,
        "notes": [
            "Only purchases that name a customer are attributed; walk-in sales are not customers.",
            "Repeat purchase rate, inactive and reactivated counts are the retention service's lifetime figures as of today, not limited to the period.",
            "A customer counts as 'new' when their first purchase ever falls in the period.",
        ],
    }


def customers(session: Session, shop_id: int, f: ReportFilters, today: date) -> ReportTable:
    """One row per purchasing customer for the period (the drill-down list behind customer revenue)."""
    per = _per_customer(session, shop_id, f)
    info = {a.customer_id: a for a in cis.list_analytics(session, shop_id, today, limit=None)}
    rows = []
    for cid, g in per.items():
        a = info.get(cid)
        if f.active is not None and a is not None and a.is_active != f.active:
            continue
        rows.append(
            {
                "customer_id": cid,
                "customer": a.name if a else f"#{cid}",
                "purchases": g["purchases"],
                "detailed": g["detailed"],
                "quick": g["quick"],
                "revenue": g["revenue"],
                "average_purchase": (g["revenue"] / g["purchases"]).quantize(Decimal("0.01")),
                "last_purchase": g["last"],
                "segments": ", ".join(s.value for s in a.segments) if a else "",
            }
        )
    rows.sort(key=lambda r: (-r["revenue"], r["customer"].casefold()))
    cols = [
        ("customer", "Customer", "text"),
        ("purchases", "Purchases", "integer"),
        ("detailed", "Detailed", "integer"),
        ("quick", "Quick", "integer"),
        ("revenue", "Revenue", "money"),
        ("average_purchase", "Average purchase", "money"),
        ("last_purchase", "Last purchase", "date"),
        ("segments", "Segments", "text"),
    ]
    return _table(
        f,
        "Customers by revenue",
        cols,
        rows,
        _HONOURED,
        "Posted sales and quick sales that name a customer; CRM segments",
        ["Revenue is before returns."],
    )


def segments(session: Session, shop_id: int, f: ReportFilters, today: date) -> ReportTable:
    """Revenue in the period by CRM segment. A customer can be in several segments, so segment rows overlap and must not be added."""
    per = _per_customer(session, shop_id, f)
    info = {a.customer_id: a for a in cis.list_analytics(session, shop_id, today, limit=None)}
    grouped: dict[str, dict] = {}
    for cid, g in per.items():
        a = info.get(cid)
        for seg in a.segments if a else []:
            s = grouped.setdefault(
                seg.value, {"segment": seg.value, "customers": 0, "revenue": ZERO, "purchases": 0}
            )
            s["customers"] += 1
            s["revenue"] += g["revenue"]
            s["purchases"] += g["purchases"]
    rows = sorted(grouped.values(), key=lambda s: (-s["revenue"], s["segment"]))
    cols = [
        ("segment", "Segment", "text"),
        ("customers", "Purchasing customers", "integer"),
        ("purchases", "Purchases", "integer"),
        ("revenue", "Revenue", "money"),
    ]
    return _table(
        f,
        "Revenue by customer segment",
        cols,
        rows,
        _HONOURED,
        "CRM segments and identified purchases",
        [
            "Segments overlap (a customer can be new and high value at once): do not add the rows.",
            "Segments describe recency, frequency, spend and credit only. Revenue is observed together with the segment; it is not caused by it.",
        ],
    )


def loyalty(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    rows_db = session.execute(
        select(LoyaltyLedger.entry_type, func.count(), func.sum(LoyaltyLedger.points_delta))
        .where(
            LoyaltyLedger.shop_id == shop_id,
            LoyaltyLedger.entry_date >= f.period.start,
            LoyaltyLedger.entry_date <= f.period.end,
        )
        .group_by(LoyaltyLedger.entry_type)
    ).all()
    rows = [{"entry_type": t.value, "entries": n, "points": int(p or 0)} for t, n, p in rows_db]
    rows.sort(key=lambda r: r["entry_type"])
    cols = [
        ("entry_type", "Entry type", "text"),
        ("entries", "Entries", "integer"),
        ("points", "Points (signed)", "integer"),
    ]
    return _table(
        f,
        "Loyalty activity",
        cols,
        rows,
        set(),
        "The loyalty ledger",
        ["Points, not money. Redeemed and expired points are negative."],
    )


def campaigns(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    """Campaigns launched in the period and what actually happened per send. With no delivery provider every send is Not Configured."""
    sent = {}
    for cid, status, n in session.execute(
        select(CampaignSend.campaign_id, CampaignSend.status, func.count())
        .where(CampaignSend.shop_id == shop_id)
        .group_by(CampaignSend.campaign_id, CampaignSend.status)
    ):
        sent.setdefault(cid, {})[status.value] = n
    rows = []
    for c in session.scalars(
        select(Campaign).where(Campaign.shop_id == shop_id).order_by(Campaign.id.desc())
    ):
        when = c.launched_at.date() if c.launched_at else c.created_at.date()
        if not f.period.contains(when):
            continue
        s = sent.get(c.id, {})
        rows.append(
            {
                "campaign_id": c.id,
                "campaign": c.name,
                "status": c.status.value,
                "channel": c.channel.value,
                "date": when,
                "audience": sum(s.values()),
                "sent": s.get("SENT", 0),
                "not_configured": s.get("NOT_CONFIGURED", 0),
                "skipped_no_consent": s.get("SKIPPED_NO_CONSENT", 0),
                "failed": s.get("FAILED", 0),
            }
        )
    cols = [
        ("campaign", "Campaign", "text"),
        ("status", "Status", "text"),
        ("channel", "Channel", "text"),
        ("date", "Date", "date"),
        ("audience", "Audience", "integer"),
        ("sent", "Sent", "integer"),
        ("not_configured", "Not configured", "integer"),
        ("skipped_no_consent", "No consent", "integer"),
        ("failed", "Failed", "integer"),
    ]
    return _table(
        f,
        "Campaign performance",
        cols,
        rows,
        set(),
        "Campaigns and their per-customer send outcomes",
        [
            "No message provider is connected: sends are recorded as Not Configured, never as delivered.",
            "No revenue is attributed to a campaign: a purchase made afterwards is not evidence the campaign caused it.",
        ],
    )


def referrals(session: Session, shop_id: int, f: ReportFilters) -> ReportTable:
    rows_db = session.execute(
        select(ReferralEvent.status, func.count())
        .where(
            ReferralEvent.shop_id == shop_id,
            ReferralEvent.created_at >= datetime.combine(f.period.start, time.min, tzinfo=UTC),
            ReferralEvent.created_at
            < datetime.combine(f.period.end + timedelta(days=1), time.min, tzinfo=UTC),
        )
        .group_by(ReferralEvent.status)
    ).all()
    rows = sorted(({"status": s.value, "referrals": n} for s, n in rows_db), key=lambda r: r["status"])
    cols = [("status", "Status", "text"), ("referrals", "Referrals", "integer")]
    return _table(
        f,
        "Referral performance",
        cols,
        rows,
        set(),
        "Referral events",
        ["A referral is rewarded only after the referred customer's qualifying purchase."],
    )
