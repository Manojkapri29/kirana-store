from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.services import ai_insights_service as insights
from app.services import business_health_service as health
from app.services import customer_intelligence_service as customers
from app.services import inventory_intelligence_service as inv
from app.services import supplier_intelligence_service as suppliers


class InventoryHealthOut(BaseModel):
    period_days: int
    total_products: int
    in_stock: int
    low_stock: int
    out_of_stock: int
    total_stock_value: Decimal | None
    products_without_cost: int
    fast_moving_count: int
    slow_moving_count: int
    dead_stock_count: int
    risk_stockout: int
    risk_overstock: int

    @classmethod
    def of(cls, h: inv.InventoryHealth) -> "InventoryHealthOut":
        return cls(**h.__dict__)


class MoverOut(BaseModel):
    product_id: int
    name: str
    sku: str
    unit_code: str
    current_stock: Decimal
    quantity_sold: Decimal
    revenue: Decimal
    days_of_cover: Decimal | None
    stock_value: Decimal | None

    @classmethod
    def of(cls, m: inv.MoverRow) -> "MoverOut":
        return cls(**m.__dict__)


class AgingOut(BaseModel):
    product_id: int
    name: str
    sku: str
    unit_code: str
    current_stock: Decimal
    days_since_last_inbound: int | None
    stock_value: Decimal | None

    @classmethod
    def of(cls, a: inv.AgingRow) -> "AgingOut":
        return cls(**a.__dict__)


class ReorderRecommendationOut(BaseModel):
    product_id: int
    name: str
    sku: str
    unit_code: str
    current_stock: Decimal
    reorder_level: Decimal
    sold_recently: Decimal
    window_days: int
    per_day: Decimal
    days_of_cover: Decimal | None
    suggested_quantity: Decimal
    supplier_id: int | None
    supplier_name: str | None
    unit_cost_used: Decimal | None
    cost_basis: str | None
    estimated_cost: Decimal | None
    reasons: list[str]
    low_history: bool
    pack_size: Decimal | None
    moq: Decimal | None
    lead_time_days: int | None

    @classmethod
    def of(cls, r: insights.Recommendation) -> "ReorderRecommendationOut":
        return cls(**r.__dict__)


class SupplierGroupOut(BaseModel):
    supplier_id: int | None
    supplier_name: str | None
    lines: list[ReorderRecommendationOut]
    estimated_total: Decimal
    lines_without_price: int

    @classmethod
    def of(cls, g: insights.SupplierGroup) -> "SupplierGroupOut":
        return cls(
            supplier_id=g.supplier_id, supplier_name=g.supplier_name,
            lines=[ReorderRecommendationOut.of(ln) for ln in g.lines],
            estimated_total=g.estimated_total, lines_without_price=g.lines_without_price,
        )  # fmt: skip


class DraftLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(ge=0)
    discount: Decimal | None = Field(default=None, ge=0)


class PurchaseDraftFromSuggestionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: int
    lines: list[DraftLineIn] = Field(min_length=1, max_length=200)
    purchase_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SupplierAnalyticsOut(BaseModel):
    supplier_id: int
    name: str
    is_active: bool
    purchase_count: int
    total_value: Decimal
    average_purchase_value: Decimal | None
    supplied_product_count: int
    first_purchase_date: date | None
    last_purchase_date: date | None
    delivery_performance_note: str

    @classmethod
    def of(cls, a: suppliers.SupplierAnalytics) -> "SupplierAnalyticsOut":
        return cls(**a.__dict__)


class PriceHistoryPointOut(BaseModel):
    purchase_id: int
    purchase_no: str | None
    purchase_date: date
    unit_cost: Decimal
    quantity: Decimal


class ProductPriceHistoryOut(BaseModel):
    product_id: int
    product_name: str
    supplier_id: int
    supplier_name: str
    points: list[PriceHistoryPointOut]
    lowest: Decimal
    highest: Decimal
    latest: Decimal
    average: Decimal

    @classmethod
    def of(cls, h: suppliers.ProductPriceHistory) -> "ProductPriceHistoryOut":
        return cls(
            product_id=h.product_id, product_name=h.product_name, supplier_id=h.supplier_id,
            supplier_name=h.supplier_name, points=[PriceHistoryPointOut(**p.__dict__) for p in h.points],
            lowest=h.lowest, highest=h.highest, latest=h.latest, average=h.average,
        )  # fmt: skip


class CustomerAnalyticsOut(BaseModel):
    customer_id: int
    name: str
    phone: str | None
    is_active: bool
    detailed_sale_count: int
    quick_sale_count: int
    total_purchases: Decimal
    average_transaction_value: Decimal | None
    first_purchase: date | None
    last_purchase: date | None
    outstanding: Decimal
    advance: Decimal
    last_payment_date: date | None
    days_since_last_purchase: int | None
    days_since_last_payment: int | None
    segments: list[str]
    online_order_count: int

    @classmethod
    def of(cls, a: customers.CustomerAnalytics) -> "CustomerAnalyticsOut":
        data = dict(a.__dict__)
        data["segments"] = [s.value for s in a.segments]
        return cls(**data)


class MetricChangeOut(BaseModel):
    label: str
    current: str
    previous: str | None
    change_percent: Decimal | None
    note: str | None

    @classmethod
    def of(cls, m: health.MetricChange) -> "MetricChangeOut":
        return cls(**m.__dict__)


class AnomalyOut(BaseModel):
    kind: str
    title: str
    detail: str
    check: str


class InsightOut(BaseModel):
    kind: str
    attention: bool
    text: str


class BusinessHealthOut(BaseModel):
    period_label: str
    previous_label: str
    metrics: list[MetricChangeOut]
    anomalies: list[AnomalyOut]
    insights: list[InsightOut]

    @classmethod
    def of(cls, r: health.HealthReport) -> "BusinessHealthOut":
        return cls(
            period_label=r.period_label, previous_label=r.previous_label,
            metrics=[MetricChangeOut.of(m) for m in r.metrics],
            anomalies=[
                AnomalyOut(kind=a.kind, title=a.title, detail=a.detail, check=a.check) for a in r.anomalies
            ],
            insights=[InsightOut(kind=i.kind, attention=i.attention, text=i.text) for i in r.insights],
        )  # fmt: skip


class DashboardOut(BaseModel):
    """The advanced dashboard: everything from the sections above, from the same read-only services, in
    one call."""

    inventory_health: InventoryHealthOut
    business_health: BusinessHealthOut
    reorder_count: int
    outstanding_total: Decimal
    customers_owing: int
