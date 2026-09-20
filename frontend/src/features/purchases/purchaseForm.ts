import type { Product, Purchase, PurchaseHeaderPayload, PurchaseItemPayload } from '@/api/types'
import { checkDecimal, isWhole, isZero } from '@/lib/decimal'
import { grossPaise, lineAmounts, toPaise, toThousandths } from '@/lib/money'

/** What the form remembers about a chosen product (enough to show it and check the quantity). */
export interface PickedProduct {
  id: number
  sku: string
  name: string
  unit_code: string
  unit_name: string
  allows_decimal: boolean
  purchase_price: string | null
  avg_cost: string | null
  current_stock: string
}

export const pickedFromProduct = (p: Product): PickedProduct => ({
  id: p.id,
  sku: p.sku,
  name: p.name,
  unit_code: p.unit_code,
  unit_name: p.unit_name,
  allows_decimal: p.unit_allows_decimal,
  purchase_price: p.purchase_price,
  avg_cost: p.avg_cost,
  current_stock: p.current_stock,
})

export interface LineValues {
  /** Local identity of the row, so React keeps the right inputs when rows are added or removed. */
  key: string
  product: PickedProduct | null
  quantity: string
  unit_cost: string
  discount: string
}

export interface HeaderValues {
  supplier_id: string
  purchase_date: string
  supplier_invoice_no: string
  notes: string
}

export type LineField = 'product' | 'quantity' | 'unit_cost' | 'discount'
export type LineErrorCode =
  | 'product'
  | 'quantityRequired'
  | 'quantityInvalid'
  | 'quantityDecimals'
  | 'quantityZero'
  | 'quantityWhole'
  | 'priceRequired'
  | 'priceInvalid'
  | 'priceDecimals'
  | 'discountInvalid'
  | 'discountDecimals'
  | 'discountTooBig'
export type LineErrors = Partial<Record<LineField, LineErrorCode>>

export type HeaderField = keyof HeaderValues
export type HeaderErrorCode = 'supplier' | 'dateRequired' | 'dateFuture'
export type HeaderErrors = Partial<Record<HeaderField, HeaderErrorCode>>

let nextKey = 0
export const newKey = () => `line-${(nextKey += 1)}`

export const emptyLine = (): LineValues => ({ key: newKey(), product: null, quantity: '', unit_cost: '', discount: '' })

/** Today as YYYY-MM-DD in the browser's timezone (the server checks again in the shop's timezone). */
export function todayText(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

export const emptyHeader = (supplierId = ''): HeaderValues => ({
  supplier_id: supplierId,
  purchase_date: todayText(),
  supplier_invoice_no: '',
  notes: '',
})

export function headerFromPurchase(p: Purchase): HeaderValues {
  return {
    supplier_id: String(p.supplier_id),
    purchase_date: p.purchase_date,
    supplier_invoice_no: p.supplier_invoice_no ?? '',
    notes: p.notes ?? '',
  }
}

/** Turn a saved draft's lines back into form rows. The product's current stock is not needed here. */
export function linesFromPurchase(p: Purchase): LineValues[] {
  return p.items.map((item) => ({
    key: newKey(),
    product: {
      id: item.product_id,
      sku: item.sku,
      name: item.product_name,
      unit_code: item.unit_code,
      unit_name: item.unit_name,
      allows_decimal: item.unit_allows_decimal,
      purchase_price: null,
      avg_cost: null,
      current_stock: '',
    },
    quantity: trimZeros(item.quantity),
    unit_cost: item.unit_cost,
    discount: isZero(item.discount) ? '' : item.discount,
  }))
}

/** "10.000" -> "10", "2.500" -> "2.5" (what a person would type). */
export function trimZeros(text: string): string {
  return text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text
}

export function validateHeader(values: HeaderValues, today: string): HeaderErrors {
  const errors: HeaderErrors = {}
  if (values.supplier_id === '') errors.supplier_id = 'supplier'
  if (values.purchase_date === '') errors.purchase_date = 'dateRequired'
  else if (values.purchase_date > today) errors.purchase_date = 'dateFuture'
  return errors
}

export function validateLine(line: LineValues): LineErrors {
  const errors: LineErrors = {}
  if (line.product === null) errors.product = 'product'

  const quantity = checkDecimal(line.quantity, 3)
  if (quantity === 'empty') errors.quantity = 'quantityRequired'
  else if (quantity === 'invalid') errors.quantity = 'quantityInvalid'
  else if (quantity === 'tooManyDecimals') errors.quantity = 'quantityDecimals'
  else if (isZero(line.quantity)) errors.quantity = 'quantityZero'
  else if (line.product && !line.product.allows_decimal && !isWhole(line.quantity)) errors.quantity = 'quantityWhole'

  const price = checkDecimal(line.unit_cost, 2)
  if (price === 'empty') errors.unit_cost = 'priceRequired'
  else if (price === 'invalid') errors.unit_cost = 'priceInvalid'
  else if (price === 'tooManyDecimals') errors.unit_cost = 'priceDecimals'

  if (line.discount.trim() !== '') {
    const discount = checkDecimal(line.discount, 2)
    if (discount === 'invalid') errors.discount = 'discountInvalid'
    else if (discount === 'tooManyDecimals') errors.discount = 'discountDecimals'
    else if (!errors.quantity && !errors.unit_cost && lineAmounts(line.quantity, line.unit_cost, line.discount) === null) {
      errors.discount = 'discountTooBig'
    }
  }
  return errors
}

/** The total of one line in paise, or `null` while the line is incomplete or invalid. */
export const lineTotalPaise = (line: LineValues): bigint | null =>
  lineAmounts(line.quantity, line.unit_cost, line.discount)?.total ?? null

/** The gross amount (before the discount) in paise, once quantity and price are valid. */
export function lineGrossPaise(line: LineValues): bigint | null {
  const q = toThousandths(line.quantity)
  const p = toPaise(line.unit_cost)
  return q === null || p === null ? null : grossPaise(q, p)
}

export const grandTotalPaise = (lines: LineValues[]): bigint =>
  lines.reduce((sum, line) => sum + (lineTotalPaise(line) ?? 0n), 0n)

export const headerPayload = (values: HeaderValues): PurchaseHeaderPayload => ({
  supplier_id: Number(values.supplier_id),
  supplier_invoice_no: values.supplier_invoice_no.trim() === '' ? null : values.supplier_invoice_no.trim(),
  purchase_date: values.purchase_date,
  notes: values.notes.trim() === '' ? null : values.notes.trim(),
})

export const itemPayloads = (lines: LineValues[]): PurchaseItemPayload[] =>
  lines.map((line) => ({
    product_id: line.product!.id,
    quantity: line.quantity.trim(),
    unit_cost: line.unit_cost.trim(),
    discount: line.discount.trim() === '' ? null : line.discount.trim(),
  }))
