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
