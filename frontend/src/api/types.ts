/** Shapes returned by the API. Money and quantities are strings, e.g. "25.50" and "2.500". */

export type StockStatus = 'IN_STOCK' | 'LOW_STOCK' | 'OUT_OF_STOCK'
export type StatusFilter = 'active' | 'inactive' | 'all'
export type MrpValidationMode = 'WARN' | 'BLOCK'

export interface Unit {
  id: number
  code: string
  name: string
  allows_decimal: boolean
}

export interface Category {
  id: number
  name: string
  is_active: boolean
}

export interface BusinessType {
  code: string
  name: string
}

/** Defaults suggested for the shop's kind of business. Suggestions only, never restrictions. */
export interface ShopTemplate {
  business_type: string
  business_type_name: string
  categories: { name: string; exists: boolean }[]
  unit_codes: string[]
}

export interface Shop {
  id: number
  /** The business name. */
  name: string
  business_type: string
  business_type_name: string
  timezone: string
  language: 'en' | 'hi'
  allow_negative_stock: boolean
  mrp_validation_mode: MrpValidationMode
  /** The shop's UPI id, when set. Shown while taking a UPI payment. */
  upi_id: string | null
}

export interface Product {
  id: number
  sku: string
  name: string
  brand: string | null
  barcode: string | null
  category_id: number
  category_name: string
  unit_id: number
  unit_code: string
  unit_name: string
  unit_allows_decimal: boolean
  default_supplier_id: number | null
  default_supplier_name: string | null
  reorder_level: string
  mrp: string | null
  selling_price: string
  purchase_price: string | null
  avg_cost: string | null
  is_active: boolean
  /** Derived from the inventory ledger. There is no such column on the product. */
  current_stock: string
  stock_status: StockStatus
  created_at: string
  updated_at: string
}

export interface ProductSaved {
  product: Product
  warnings: string[]
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface ProductPayload {
  sku: string
  name: string
  brand: string | null
  category_id: number
  unit_id: number
  default_supplier_id: number | null
  reorder_level: string
  mrp: string | null
  selling_price: string
  purchase_price: string | null
  barcode: string | null
}

export interface ProductCreatePayload extends ProductPayload {
  opening_stock?: string | null
  opening_stock_cost?: string | null
}

export interface InventoryItem {
  product_id: number
  sku: string
  name: string
  brand: string | null
  barcode: string | null
  category_name: string
  unit_code: string
  unit_name: string
  allows_decimal: boolean
  current_stock: string
  reorder_level: string
  avg_cost: string | null
  status: StockStatus
  is_active: boolean
}

export type TransactionType =
  | 'OPENING'
  | 'PURCHASE'
  | 'SALE'
  | 'SALE_RETURN'
  | 'PURCHASE_RETURN'
  | 'ADJUSTMENT'
  | 'REVERSAL'

export type ReasonCode =
  | 'CUSTOMER_RETURN_NO_BILL'
  | 'COUNT_CORRECTION'
  | 'DAMAGED'
  | 'EXPIRED'
  | 'LOST'
  | 'OTHER'

export interface InventoryTransaction {
  id: number
  product_id: number
  sku: string
  product_name: string
  txn_type: TransactionType
  qty_delta: string
  balance_after: string
  unit_cost: string | null
  txn_date: string
  reference_type: string | null
  reference_id: number | null
  reason_code: ReasonCode | null
  note: string | null
  created_by_name: string
  created_at: string
  /** Set when the row came from a purchase line (or from voiding one). */
  purchase_id: number | null
  purchase_no: string | null
  /** Set when the row came from a sale line (or from voiding one). */
  sale_id: number | null
  sale_no: string | null
}

export interface OpeningStockPayload {
  product_id: number
  quantity: string
  unit_cost?: string | null
  note?: string | null
}

export interface OpeningStockResult {
  transaction: InventoryTransaction
  stock: {
    product_id: number
    current_stock: string
    status: StockStatus
  }
}

export interface Supplier {
  id: number
  name: string
  phone: string | null
  alternate_phone: string | null
  email: string | null
  address: string | null
  gstin: string | null
  notes: string | null
  is_active: boolean
  /** Products that name this supplier as their default supplier. */
  product_count: number
  created_at: string
  updated_at: string
}

export interface SupplierSaved {
  supplier: Supplier
  warnings: string[]
}

/** The editable details of a supplier. Blank optional fields are sent as null. */
export interface SupplierPayload {
  name: string
  phone: string | null
  alternate_phone: string | null
  email: string | null
  address: string | null
  gstin: string | null
  notes: string | null
}

export interface SupplierOption {
  id: number
  name: string
}

export type ExportFormat = 'csv' | 'xlsx'

export type PurchaseStatus = 'DRAFT' | 'POSTED' | 'VOID'

export interface PurchaseInventoryEffect {
  id: number
  txn_type: TransactionType
  qty_delta: string
  txn_date: string
}

export interface PurchaseItem {
  id: number
  product_id: number
  sku: string
  product_name: string
  unit_id: number
  unit_code: string
  unit_name: string
  unit_allows_decimal: boolean
  quantity: string
  unit_cost: string
  discount: string
  line_total: string
  /** Snapshot taken when the purchase was posted; null on a draft. A null cost means "unknown". */
  stock_before: string | null
  stock_after: string | null
  avg_cost_before: string | null
  avg_cost_after: string | null
  inventory_effects: PurchaseInventoryEffect[]
}

export interface Purchase {
  id: number
  /** Assigned when posted, e.g. PUR/2026-27/0001. A draft has none. */
  purchase_no: string | null
  status: PurchaseStatus
  supplier_id: number
  supplier_name: string
  supplier_invoice_no: string | null
  purchase_date: string
  notes: string | null
  total_amount: string
  item_count: number
  created_by_name: string
  created_at: string
  updated_at: string
  posted_at: string | null
  posted_by_name: string | null
  void_reason: string | null
  voided_at: string | null
  replaces_id: number | null
  replaced_by_id: number | null
  items: PurchaseItem[]
}

/** One row of the purchase list (no lines). */
export interface PurchaseSummary {
  id: number
  purchase_no: string | null
  status: PurchaseStatus
  supplier_id: number
  supplier_name: string
  supplier_invoice_no: string | null
  purchase_date: string
  total_amount: string
  item_count: number
  created_by_name: string
  created_at: string
}

export interface PurchaseItemPayload {
  product_id: number
  quantity: string
  unit_cost: string
  discount: string | null
}

export interface PurchaseHeaderPayload {
  supplier_id: number
  supplier_invoice_no: string | null
  purchase_date: string
  notes: string | null
}

export interface SupplierPurchaseTotals {
  posted_count: number
  posted_total: string
}

export type BalanceStatus = 'OUTSTANDING' | 'SETTLED' | 'ADVANCE'
export type BalanceFilter = 'any' | 'outstanding' | 'settled' | 'advance'
export type LedgerEntryType =
  | 'OPENING_BALANCE'
  | 'CREDIT_SALE'
  | 'PAYMENT'
  | 'RETURN_CREDIT'
  | 'ADJUSTMENT'
  | 'REVERSAL'
export type PaymentMethod = 'CASH' | 'UPI' | 'OTHER'

/** A customer with the balance the ledger gives them. `balance` is signed: positive = owes, negative = advance. */
export interface Customer {
  id: number
  name: string
  phone: string | null
  email: string | null
  address: string | null
  notes: string | null
  is_active: boolean
  balance: string
  outstanding: string
  advance: string
  balance_status: BalanceStatus
  entry_count: number
  created_at: string
  updated_at: string
}

export interface CustomerPayload {
  name: string
  phone: string | null
  email: string | null
  address: string | null
  notes: string | null
}

export interface LedgerEntry {
  id: number
  customer_id: number
  entry_date: string
  entry_type: LedgerEntryType
  /** Signed: positive = the customer owes more, negative = owes less. */
  amount_delta: string
  /** The running balance after this entry. */
  balance_after: string
  payment_method: PaymentMethod | null
  payment_reference: string | null
  reference_type: string | null
  reference_id: number | null
  /** The sale's invoice number, for entries that came from a sale. */
  reference_no: string | null
  reverses_entry_id: number | null
  reversed_by_entry_id: number | null
  note: string | null
  created_by_name: string
  created_at: string
}

export interface CustomerSaved {
  customer: Customer
  warnings: string[]
  opening_entry: LedgerEntry | null
}

export interface BalanceSummary {
  customer_id: number
  balance: string
  outstanding: string
  advance: string
  status: BalanceStatus
}

export interface KhataEntryResult {
  entry: LedgerEntry
  balance: BalanceSummary
}

export type SaleStatus = 'DRAFT' | 'POSTED' | 'VOID'
export type PaymentType = 'PAID' | 'CREDIT'

export interface SaleInventoryEffect {
  id: number
  txn_type: TransactionType
  qty_delta: string
  txn_date: string
}

export interface SaleItem {
  id: number
  product_id: number
  sku: string
  product_name: string
  unit_id: number
  unit_code: string
  unit_name: string
  unit_allows_decimal: boolean
  quantity: string
  unit_price: string
  mrp: string | null
  discount: string
  /** Quantity x price, before the line discount. */
  gross: string
  /** The line after the cashier's own discount. */
  line_total: string
  /** This line's share of what offers took off the bill. */
  promotion_discount: string
  /** line_total less promotion_discount: what the line really earned. */
  net_total: string
  /** Cost snapshot at posting. Null = unknown (never 0), and then the profit is unknown too. */
  unit_cost: string | null
  cogs_amount: string | null
  profit: string | null
  inventory_effects: SaleInventoryEffect[]
}

export interface Sale {
  id: number
  /** Assigned when posted, e.g. INV/2026-27/0001. A draft has none. */
  invoice_no: string | null
  status: SaleStatus
  customer_id: number | null
  customer_name: string | null
  sale_date: string
  notes: string | null
  subtotal: string
  /** The cashier's own amount off the whole bill. */
  discount: string
  /** What offers and coupons took off (see `promotions`). */
  promotion_discount: string
  coupon_code: string | null
  total_amount: string
  payment_type: PaymentType | null
  amount_paid: string | null
  /** The part of the bill that is on the customer's khata. */
  credit_amount: string
  payment_method: PaymentMethod | null
  payment_reference: string | null
  item_count: number
  created_by_name: string
  created_at: string
  updated_at: string
  posted_at: string | null
  posted_by_name: string | null
  void_reason: string | null
  voided_at: string | null
  replaces_id: number | null
  replaced_by_id: number | null
  /** Null unless the sale is posted AND every line's cost was known. */
  cogs_total: string | null
  gross_profit: string | null
  lines_without_cost: number
  warnings: string[]
  /** The offers the bill got: the frozen snapshot once posted, the current worth while a draft. */
  promotions: AppliedPromotion[]
  /** A draft whose offers are worth something else now than when last saved. */
  promotions_out_of_date: boolean
  items: SaleItem[]
}

export interface SaleSummary {
  id: number
  invoice_no: string | null
  status: SaleStatus
  customer_id: number | null
  customer_name: string | null
  sale_date: string
  total_amount: string
  amount_paid: string | null
  payment_type: PaymentType | null
  payment_method: PaymentMethod | null
  item_count: number
  created_by_name: string
  created_at: string
}

export interface SaleItemPayload {
  product_id: number
  quantity: string
  /** Leave null to use the product's own price. */
  unit_price: string | null
  discount: string | null
}

export interface SaleHeaderPayload {
  customer_id: number | null
  sale_date: string
  notes: string | null
  discount: string | null
  coupon_code: string | null
}

export interface SalePaymentPayload {
  /** Null = paid in full. */
  amount_paid: string | null
  payment_method: PaymentMethod | null
  payment_reference: string | null
}

export interface FieldProblem {
  field: string
  message: string
}

export interface SalePreviewLine {
  product_id: number | null
  quantity: string | null
  unit_price: string | null
  discount: string
  gross: string | null
  line_total: string | null
  promotion_discount: string
  /** Stock on hand right now. */
  available: string | null
  /** More is wanted (across all lines of that product) than is on hand: fine for a draft, not for posting. */
  short: boolean
  errors: FieldProblem[]
}

/** The priced cart, exactly as posting would compute it. The screen shows these and does no arithmetic. */
export interface SalePreview {
  lines: SalePreviewLine[]
  subtotal: string
  /** The cashier's own bill discount. */
  discount: string
  /** What offers and coupons take off. */
  promotion_discount: string
  total: string
  promotions: AppliedPromotion[]
  /** Offers considered but not applied, each with the reason. */
  not_applied: { promotion_id: number | null; name: string; reason: string }[]
  coupon: CouponResult | null
  payment_type: PaymentType
  paid: string
  credit: string
  errors: FieldProblem[]
  warnings: string[]
}

// --- Phase 8: offers, Quick Sales, plans, price checks, reports -----------------------------------------------

export type PromotionType = 'PERCENT' | 'AMOUNT' | 'OFFER_PRICE' | 'BUY_X_GET_Y'
export type PromotionScope = 'CART' | 'PRODUCTS' | 'CATEGORIES'
export type PromotionStatus = 'DRAFT' | 'ACTIVE' | 'PAUSED' | 'EXPIRED'
export type PromotionAudience = 'ALL' | 'NEW_CUSTOMER' | 'CUSTOMERS'

/** What one offer gave a bill. On a posted sale this is a frozen snapshot, so it never changes. */
export interface AppliedPromotion {
  promotion_id: number
  name: string
  promo_type: PromotionType
  /** The offer in a few words, e.g. "10% off". */
  terms: string
  coupon_code: string | null
  amount: string
  /** Why it applied. */
  basis: string
}

export interface CouponResult {
  code: string
  applied: boolean
  message: string
}

export interface Promotion {
  id: number
  name: string
  description: string | null
  promo_type: PromotionType
  scope: PromotionScope
  status: PromotionStatus
  effective_status: PromotionStatus
  /** Active and inside its dates: the only state in which it can apply. */
  is_live: boolean
  terms: string
  priority: number
  stackable: boolean
  starts_at: string | null
  ends_at: string | null
  coupon_code: string | null
  audience: PromotionAudience
  percent: string | null
  amount: string | null
  offer_price: string | null
  buy_quantity: number | null
  get_quantity: number | null
  get_percent: string | null
  min_cart_value: string | null
  min_quantity: string | null
  max_discount: string | null
  usage_limit: number | null
  per_customer_limit: number | null
  product_ids: number[]
  category_ids: number[]
  customer_ids: number[]
  products: string[]
  categories: string[]
  customers: string[]
  used_count: number
  discount_given: string
  created_by_name: string
  created_at: string
  updated_at: string
}

/** What can be sent when creating or changing an offer. Percentages and money are text. */
export interface PromotionPayload {
  name?: string
  description?: string | null
  promo_type?: PromotionType
  scope?: PromotionScope
  priority?: number
  stackable?: boolean
  starts_at?: string | null
  ends_at?: string | null
  coupon_code?: string | null
  audience?: PromotionAudience
  percent?: string | null
  amount?: string | null
  offer_price?: string | null
  buy_quantity?: number | null
  get_quantity?: number | null
  get_percent?: string | null
  min_cart_value?: string | null
  min_quantity?: string | null
  max_discount?: string | null
  usage_limit?: number | null
  per_customer_limit?: number | null
  product_ids?: number[]
  category_ids?: number[]
  customer_ids?: number[]
}

export interface PromotionUsage {
  sale_id: number
  invoice_no: string | null
  sale_date: string
  sale_status: SaleStatus
  customer_name: string | null
  promotion_id: number
  name: string
  terms: string
  coupon_code: string | null
  discount_amount: string
  basis: string
}

export interface QuickSale {
  id: number
  quick_no: string | null
  status: SaleStatus
  sale_date: string
  customer_id: number | null
  customer_name: string | null
  gross_amount: string
  discount: string
  total_amount: string
  payment_type: PaymentType | null
  amount_paid: string | null
  credit_amount: string
  payment_method: PaymentMethod | null
  payment_reference: string | null
  note: string | null
  created_by_name: string
  created_at: string
  posted_at: string | null
  posted_by_name: string | null
  void_reason: string | null
  voided_at: string | null
  /** Always null: a Quick Sale has no product and no cost. */
  gross_profit: null
  /** Always "Not Available". */
  profit_label: string
}

export interface QuickSalePayload {
  gross_amount?: string
  discount?: string | null
  customer_id?: number | null
  sale_date?: string | null
  note?: string | null
}

export interface PlanLimitUsage {
  products: number
  users: number
  invoices: number
  price_lookups: number
}

export interface SubscriptionPlan {
  code: string
  name: string
  description: string | null
  price: string | null
  currency: string
  billing_interval: 'MONTHLY' | 'YEARLY'
  features: Record<string, boolean>
  limits: Record<string, number | null>
  is_current: boolean
}

export interface Subscription {
  plan_code: string
  plan_name: string
  source: string
  status: string | null
  ends_at: string | null
  features: Record<string, boolean>
  /** null = unlimited. */
  limits: Record<string, number | null>
  usage: PlanLimitUsage
  period: string
  plans: SubscriptionPlan[]
}

export interface LookupProduct extends Product {
  /** A promotional price the product has right now. Shown beside MRP and selling price, never replacing them. */
  offer: { promotion_id: number; name: string; offer_price: string } | null
}

export interface LookupResult {
  code: string
  found: boolean
  match_type: 'BARCODE' | 'SKU' | 'NAME' | 'SEARCH' | 'NONE'
  /** "Barcode not found" when nothing matched. */
  message: string | null
  products: LookupProduct[]
}

export type ProviderState = 'LIVE' | 'CACHED' | 'STALE' | 'NO_DATA' | 'NOT_CONFIGURED' | 'DISABLED' | 'UNAVAILABLE'

export interface PriceQuote {
  product_name: string | null
  matched_product: { id: number; name: string; sku: string; barcode: string | null; selling_price: string; mrp: string | null } | null
  barcode: string
  price: string
  currency: string
  source: string
  source_label: string
  source_url: string | null
  location: string | null
  location_matched: boolean | null
  observed_on: string | null
  checked_at: string | null
  match_type: 'EXACT' | 'POSSIBLE'
  match_label: string
  match_basis: string
  confidence: number
  stale: boolean
  currency_matches_shop: boolean
  difference: string | null
}

export interface PriceResult {
  barcode: string
  product: { id: number; name: string; sku: string; selling_price: string; mrp: string | null } | null
  quotes: PriceQuote[]
  providers: { name: string; label: string; state: ProviderState; message: string; checked_at: string | null }[]
  location: { city: string | null; state: string | null; market: string | null; applied: boolean; note: string }
  identified_as: { name: string | null; brand: string | null; pack_text: string | null } | null
  notes: string[]
  changes_prices: false
}

export interface ReportTotals {
  sales_count: number
  gross_sales: string
  line_discount: string
  bill_discount: string
  promotion_discount: string
  discount: string
  net_sales: string
}

export interface SalesSummary {
  date_from: string
  date_to: string
  detailed: ReportTotals
  quick: ReportTotals
  combined: ReportTotals
  detailed_gross_profit: string | null
  detailed_sales_without_cost: number
  combined_profit: null
  combined_profit_label: string
  days: { day: string; detailed: ReportTotals; quick: ReportTotals; combined: ReportTotals }[]
}

export interface DiscountReport {
  date_from: string
  date_to: string
  gross_sales: string
  net_sales: string
  total_discount: string
  line_discount: string
  bill_discount: string
  promotion_discount: string
  promotions_used: number
  promotion_applications: number
  coupon_uses: number
  coupon_discount: string
  by_promotion: { promotion_id: number; name: string; uses: number; discount: string }[]
  by_coupon: { code: string; uses: number; discount: string }[]
  by_date: { day: string; line_discount: string; bill_discount: string; promotion_discount: string; total: string }[]
}
