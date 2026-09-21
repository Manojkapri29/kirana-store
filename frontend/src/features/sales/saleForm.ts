/**
 * Billing cart logic: the values a cashier types, quick *format* checks, and conversion to the API payload.
 *
 * There is deliberately NO money arithmetic here. Line totals, the subtotal, discounts, the total and how much
 * goes on the khata all come from the server (`/sales/calculate`), which is the same code that posting uses.
 * Format checks only decide whether a typed value can be sent at all; the server validates again.
 */

import type { PaymentMethod, Product, Sale, SaleItemPayload, SalePaymentPayload } from '@/api/types'
import { checkDecimal, isWhole, isZero } from '@/lib/decimal'

/** What the cart remembers about a chosen product (enough to show it and check the quantity's format). */
export interface CartProduct {
  id: number
  sku: string
  name: string
  unit_code: string
  allows_decimal: boolean
}

export interface CartLine {
  /** Local identity of the row, so React keeps the right inputs when rows are added or removed. */
  key: string
  product: CartProduct
  quantity: string
  /** The price per unit. Starts as the product's own price; the cashier may change it. */
  unit_price: string
  discount: string
}

let nextKey = 0
export const newKey = () => `cart-${(nextKey += 1)}`

export const cartProduct = (p: Product): CartProduct => ({
  id: p.id,
  sku: p.sku,
  name: p.name,
  unit_code: p.unit_code,
  allows_decimal: p.unit_allows_decimal,
})

export const lineFromProduct = (p: Product): CartLine => ({
  key: newKey(),
  product: cartProduct(p),
  quantity: '1',
  unit_price: p.selling_price,
  discount: '',
})

/** "10.000" -> "10", "2.500" -> "2.5" (what a person would type). */
export const trimZeros = (text: string): string =>
  text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text

export const linesFromSale = (sale: Sale): CartLine[] =>
  sale.items.map((item) => ({
    key: newKey(),
    product: {
      id: item.product_id,
      sku: item.sku,
      name: item.product_name,
      unit_code: item.unit_code,
      allows_decimal: item.unit_allows_decimal,
    },
    quantity: trimZeros(item.quantity),
    unit_price: item.unit_price,
    discount: isZero(item.discount) ? '' : item.discount,
  }))

export type LineField = 'quantity' | 'unit_price' | 'discount'
export type LineErrorCode =
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
export type LineErrors = Partial<Record<LineField, LineErrorCode>>

/** Can this line be sent to the server at all? (Format only: amounts are never added up here.) */
export function lineFormatErrors(line: CartLine): LineErrors {
  const errors: LineErrors = {}
  const quantity = checkDecimal(line.quantity, 3)
  if (quantity === 'empty') errors.quantity = 'quantityRequired'
  else if (quantity === 'invalid') errors.quantity = 'quantityInvalid'
  else if (quantity === 'tooManyDecimals') errors.quantity = 'quantityDecimals'
  else if (isZero(line.quantity)) errors.quantity = 'quantityZero'
  else if (!line.product.allows_decimal && !isWhole(line.quantity)) errors.quantity = 'quantityWhole'

  const price = checkDecimal(line.unit_price, 2)
  if (price === 'empty') errors.unit_price = 'priceRequired'
  else if (price === 'invalid') errors.unit_price = 'priceInvalid'
  else if (price === 'tooManyDecimals') errors.unit_price = 'priceDecimals'

  if (line.discount.trim() !== '') {
    const discount = checkDecimal(line.discount, 2)
    if (discount === 'invalid') errors.discount = 'discountInvalid'
    else if (discount === 'tooManyDecimals') errors.discount = 'discountDecimals'
  }
  return errors
}

export const isSendable = (line: CartLine): boolean => Object.keys(lineFormatErrors(line)).length === 0

export const itemPayload = (line: CartLine): SaleItemPayload => ({
  product_id: line.product.id,
  quantity: line.quantity.trim(),
  unit_price: line.unit_price.trim(),
  discount: line.discount.trim() === '' ? null : line.discount.trim(),
})

export type PaymentMode = 'full' | 'part'

export interface PaymentValue {
  mode: PaymentMode
  /** Used when `mode` is "part": what the customer actually hands over now (0 = all on credit). */
  amount: string
  method: PaymentMethod | ''
  reference: string
}

export const emptyPayment = (): PaymentValue => ({ mode: 'full', amount: '', method: 'CASH', reference: '' })

/** Is any money being received now? (Full payment always is; a part payment only if the amount is not zero.) */
export const receivesMoney = (value: PaymentValue): boolean =>
  value.mode === 'full' || (value.amount.trim() !== '' && !isZero(value.amount))

/** The amount to ask the server about: null means "in full". Only a well-formed amount is sent. */
export function amountPaidFor(value: PaymentValue): string | null {
  if (value.mode === 'full') return null
  return checkDecimal(value.amount, 2) === 'ok' ? value.amount.trim() : null
}

export function paymentPayload(value: PaymentValue): SalePaymentPayload {
  return {
    amount_paid: amountPaidFor(value),
    payment_method: receivesMoney(value) && value.method !== '' ? value.method : null,
    payment_reference: value.reference.trim() === '' ? null : value.reference.trim(),
  }
}

/** A part payment needs a well-formed amount (zero is fine: all on credit). */
export const partAmountProblem = (value: PaymentValue): 'invalid' | 'decimals' | 'required' | null => {
  if (value.mode !== 'part') return null
  const check = checkDecimal(value.amount, 2)
  if (check === 'empty') return 'required'
  if (check === 'invalid') return 'invalid'
  return check === 'tooManyDecimals' ? 'decimals' : null
}

/** Today as YYYY-MM-DD in the browser's timezone (the server checks again in the shop's timezone). */
export function todayText(): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}
