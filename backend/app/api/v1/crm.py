"""CRM: customer profile, notes, timeline, classification/consent, segmentation and groups. See
`crm_service` and `crm_segment_service` for every rule; this router only parses requests, opens the write
transaction, and shapes the response."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Ctx
from app.db.session import get_session, write_transaction
from app.schemas.crm import (
    ApprovalSettingsIO,
    ClassificationIn,
    CreateManualGroupIn,
    CreateRuleGroupIn,
    CustomerAnalyticsListOut,
    CustomerAnalyticsOut,
    CustomerGroupListOut,
    CustomerGroupOut,
    CustomerProfileOut,
    NoteIn,
    NoteOut,
    SetMembersIn,
    TimelineEventOut,
)
from app.services import crm_segment_service, crm_service
from app.services import customer_intelligence_service as cis
from app.services.shop_service import get_shop, shop_today

router = APIRouter(prefix="/crm", tags=["crm"])
ReadSession = Annotated[Session, Depends(get_session)]


def _today(session: Session, shop_id: int) -> date:
    return shop_today(get_shop(session, shop_id))


@router.get("/customers/{customer_id}/profile", response_model=CustomerProfileOut)
def get_profile(customer_id: int, ctx: Ctx, session: ReadSession) -> CustomerProfileOut:
    return CustomerProfileOut.of(
        crm_service.get_profile(session, ctx.shop_id, customer_id, _today(session, ctx.shop_id))
    )


@router.get("/customers/{customer_id}/timeline", response_model=list[TimelineEventOut])
def get_timeline(
    customer_id: int, ctx: Ctx, session: ReadSession, limit: Annotated[int, Query(ge=1, le=500)] = 200
) -> list[TimelineEventOut]:
    return [
        TimelineEventOut.of(e)
        for e in crm_service.get_timeline(session, ctx.shop_id, customer_id, limit=limit)
    ]


@router.patch("/customers/{customer_id}/classification", response_model=CustomerProfileOut)
def update_classification(customer_id: int, payload: ClassificationIn, ctx: Ctx) -> CustomerProfileOut:
    with write_transaction() as session:
        crm_service.update_classification(session, ctx, customer_id, payload.model_dump(exclude_unset=True))
        return CustomerProfileOut.of(
            crm_service.get_profile(session, ctx.shop_id, customer_id, _today(session, ctx.shop_id))
        )


@router.get("/customers/{customer_id}/notes", response_model=list[NoteOut])
def list_notes(customer_id: int, ctx: Ctx, session: ReadSession) -> list[NoteOut]:
    return [NoteOut.of(n) for n in crm_service.list_notes(session, ctx.shop_id, customer_id)]


@router.post("/customers/{customer_id}/notes", response_model=NoteOut, status_code=201)
def add_note(customer_id: int, payload: NoteIn, ctx: Ctx) -> NoteOut:
    with write_transaction() as session:
        return NoteOut.of(crm_service.add_note(session, ctx, customer_id, payload.body))


@router.get("/segments", response_model=CustomerAnalyticsListOut)
def list_segment(
    ctx: Ctx,
    session: ReadSession,
    segment: cis.CustomerSegment | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerAnalyticsListOut:
    today = _today(session, ctx.shop_id)
    rows = cis.list_analytics(session, ctx.shop_id, today, segment=segment, limit=None)
    page = rows[offset : offset + limit]
    return CustomerAnalyticsListOut(
        items=[CustomerAnalyticsOut.of(r) for r in page], total=len(rows), limit=limit, offset=offset
    )


@router.get("/groups", response_model=CustomerGroupListOut)
def list_groups(ctx: Ctx, session: ReadSession) -> CustomerGroupListOut:
    groups = crm_segment_service.list_groups(session, ctx.shop_id)
    return CustomerGroupListOut(
        items=[
            CustomerGroupOut.of(
                g, member_count=len(crm_segment_service.members_of(session, ctx.shop_id, g.id).customer_ids)
            )
            for g in groups
        ]
    )


@router.post("/groups/manual", response_model=CustomerGroupOut, status_code=201)
def create_manual_group(payload: CreateManualGroupIn, ctx: Ctx) -> CustomerGroupOut:
    with write_transaction() as session:
        group = crm_segment_service.create_manual_group(
            session, ctx, name=payload.name, customer_ids=payload.customer_ids
        )
        return CustomerGroupOut.of(group, member_count=len(payload.customer_ids))


@router.post("/groups/rule-based", response_model=CustomerGroupOut, status_code=201)
def create_rule_group(payload: CreateRuleGroupIn, ctx: Ctx) -> CustomerGroupOut:
    with write_transaction() as session:
        group = crm_segment_service.create_rule_based_group(
            session, ctx, name=payload.name, rule=payload.rule, today=_today(session, ctx.shop_id)
        )
        member_count = len(crm_segment_service.members_of(session, ctx.shop_id, group.id).customer_ids)
        return CustomerGroupOut.of(group, member_count=member_count)


@router.get("/groups/{group_id}", response_model=CustomerGroupOut)
def get_group(group_id: int, ctx: Ctx, session: ReadSession) -> CustomerGroupOut:
    group = crm_segment_service.get_group(session, ctx.shop_id, group_id)
    member_count = len(crm_segment_service.members_of(session, ctx.shop_id, group_id).customer_ids)
    return CustomerGroupOut.of(group, member_count=member_count)


@router.get("/groups/{group_id}/members", response_model=list[int])
def get_members(group_id: int, ctx: Ctx, session: ReadSession) -> list[int]:
    return crm_segment_service.members_of(session, ctx.shop_id, group_id).customer_ids


@router.put("/groups/{group_id}/members", response_model=CustomerGroupOut)
def set_members(group_id: int, payload: SetMembersIn, ctx: Ctx) -> CustomerGroupOut:
    with write_transaction() as session:
        group = crm_segment_service.set_manual_members(session, ctx, group_id, payload.customer_ids)
        return CustomerGroupOut.of(group, member_count=len(payload.customer_ids))


@router.post("/groups/{group_id}/recalculate", response_model=CustomerGroupOut)
def recalculate_group(group_id: int, ctx: Ctx) -> CustomerGroupOut:
    with write_transaction() as session:
        group = crm_segment_service.recalculate(session, ctx, group_id, today=_today(session, ctx.shop_id))
        member_count = len(crm_segment_service.members_of(session, ctx.shop_id, group_id).customer_ids)
        return CustomerGroupOut.of(group, member_count=member_count)


@router.get("/approval-settings", response_model=ApprovalSettingsIO)
def get_approval_settings(ctx: Ctx, session: ReadSession) -> ApprovalSettingsIO:
    return ApprovalSettingsIO(**crm_service.get_approval_settings(session, ctx.shop_id))


@router.put("/approval-settings", response_model=ApprovalSettingsIO)
def set_approval_settings(payload: ApprovalSettingsIO, ctx: Ctx) -> ApprovalSettingsIO:
    with write_transaction() as session:
        return ApprovalSettingsIO(
            **crm_service.set_approval_settings(
                session,
                ctx,
                campaign_audience_threshold=payload.campaign_audience_threshold,
                loyalty_adjustment_threshold=payload.loyalty_adjustment_threshold,
            )
        )
