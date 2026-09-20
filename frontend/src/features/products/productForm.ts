/**
 * Product form logic: values, validation, and conversion to API payloads.
 *
 * Pure functions with no React in them. All amounts are strings; nothing is converted to a JavaScript
 * number. This is convenience validation so the user gets instant feedback. The server enforces every rule
 * again, and its messages are shown when they differ.
 */

import type { Product, ProductCreatePayload, ProductPayload, Unit } from '@/api/types'
import { checkDecimal, decimalGreaterThan, isWhole, isZero } from '@/lib/decimal'

export interface FormValues {
  sku: string
  name: string
  brand: string
  categoryId: string
  unitId: string
  reorderLevel: string
  mrp: string
  sellingPrice: string
  purchasePrice: string
  barcode: string
  openingStock: string
  openingStockCost: string
}

export type FieldName = keyof FormValues

export type ErrorCode = 'required' | 'invalidNumber' | 'tooManyDecimals' | 'wholeNumber' | 'stockNeedsQuantity'

export interface FieldError {
  code: ErrorCode
  params?: Record<string, string | number>
}

export type FieldErrors = Partial<Record<FieldName, FieldError>>

export const MONEY_DECIMALS = 2
export const QUANTITY_DECIMALS = 3

export const emptyValues = (): FormValues => ({
  sku: '',
  name: '',
  brand: '',
  categoryId: '',
  unitId: '',
  reorderLevel: '0',
  mrp: '',
  sellingPrice: '',
  purchasePrice: '',
  barcode: '',
  openingStock: '',
  openingStockCost: '',
})

/** "5.000" -> "5", "2.500" -> "2.5": friendlier to edit than the padded API value. */
export const trimZeros = (value: string): string => (value.includes('.') ? value.replace(/\.?0+$/, '') : value)

export const valuesFromProduct = (product: Product): FormValues => ({
  sku: product.sku,
  name: product.name,
  brand: product.brand ?? '',
  categoryId: String(product.category_id),
  unitId: String(product.unit_id),
  reorderLevel: trimZeros(product.reorder_level),
  mrp: product.mrp ?? '',
  sellingPrice: product.selling_price,
  purchasePrice: product.purchase_price ?? '',
  barcode: product.barcode ?? '',
  openingStock: '',
  openingStockCost: '',
})

function checkAmount(text: string, decimals: number, required: boolean): FieldError | undefined {
  switch (checkDecimal(text, decimals)) {
    case 'empty':
      return required ? { code: 'required' } : undefined
    case 'invalid':
      return { code: 'invalidNumber' }
    case 'tooManyDecimals':
      return { code: 'tooManyDecimals', params: { places: decimals } }
    default:
      return undefined
  }
}

export function validate(values: FormValues, unit: Unit | undefined, mode: 'create' | 'edit'): FieldErrors {
  const errors: FieldErrors = {}
  const set = (field: FieldName, error: FieldError | undefined) => {
    if (error) errors[field] = error
  }

  if (!values.sku.trim()) errors.sku = { code: 'required' }
  if (!values.name.trim()) errors.name = { code: 'required' }
  if (!values.categoryId) errors.categoryId = { code: 'required' }
  if (!values.unitId) errors.unitId = { code: 'required' }

  set('sellingPrice', checkAmount(values.sellingPrice, MONEY_DECIMALS, true))
  set('mrp', checkAmount(values.mrp, MONEY_DECIMALS, false))
  set('purchasePrice', checkAmount(values.purchasePrice, MONEY_DECIMALS, false))
  set('reorderLevel', checkAmount(values.reorderLevel, QUANTITY_DECIMALS, true))

  const wholeOnly = unit !== undefined && !unit.allows_decimal
  const wholeNumberError = (): FieldError => ({ code: 'wholeNumber', params: { unit: unit?.name ?? '' } })
  if (!errors.reorderLevel && wholeOnly && !isWhole(values.reorderLevel)) errors.reorderLevel = wholeNumberError()

  if (mode === 'create') {
    set('openingStock', checkAmount(values.openingStock, QUANTITY_DECIMALS, false))
    set('openingStockCost', checkAmount(values.openingStockCost, MONEY_DECIMALS, false))
    if (!errors.openingStock && values.openingStock.trim() && wholeOnly && !isWhole(values.openingStock)) {
      errors.openingStock = wholeNumberError()
    }
    if (values.openingStockCost.trim() && (!values.openingStock.trim() || isZero(values.openingStock))) {
      errors.openingStock = errors.openingStock ?? { code: 'stockNeedsQuantity' }
    }
  }
  return errors
}

/** True when the selling price and MRP are both valid and the price is above the MRP. */
export function sellingAboveMrp(values: FormValues): boolean {
  if (checkDecimal(values.sellingPrice, MONEY_DECIMALS) !== 'ok' || checkDecimal(values.mrp, MONEY_DECIMALS) !== 'ok') {
    return false
  }
  return decimalGreaterThan(values.sellingPrice, values.mrp)
}

const orNull = (value: string): string | null => (value.trim() === '' ? null : value.trim())

function basePayload(values: FormValues): ProductPayload {
  return {
    sku: values.sku.trim(),
    name: values.name.trim(),
    brand: orNull(values.brand),
    category_id: Number(values.categoryId),
    unit_id: Number(values.unitId),
    reorder_level: values.reorderLevel.trim(),
    mrp: orNull(values.mrp),
    selling_price: values.sellingPrice.trim(),
    purchase_price: orNull(values.purchasePrice),
    barcode: orNull(values.barcode),
  }
}

export function buildCreatePayload(values: FormValues): ProductCreatePayload {
  const payload: ProductCreatePayload = basePayload(values)
  if (values.openingStock.trim() && !isZero(values.openingStock)) {
    payload.opening_stock = values.openingStock.trim()
    payload.opening_stock_cost = orNull(values.openingStockCost)
  }
  return payload
}

/** Same number, ignoring trailing zeros ("5" and "5.000", "250.5" and "250.50"). */
function sameAmount(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return a === b
  return !decimalGreaterThan(a, b) && !decimalGreaterThan(b, a)
}

/** Only the fields that differ from the saved product, so an edit never touches anything else. */
export function buildUpdatePayload(values: FormValues, original: Product): Partial<ProductPayload> {
  const next = basePayload(values)
  const changes: Partial<ProductPayload> = {}

  if (next.sku.toUpperCase() !== original.sku) changes.sku = next.sku
  if (next.name !== original.name) changes.name = next.name
  if (next.brand !== original.brand) changes.brand = next.brand
  if (next.category_id !== original.category_id) changes.category_id = next.category_id
  if (next.unit_id !== original.unit_id) changes.unit_id = next.unit_id
  if (!sameAmount(next.reorder_level, original.reorder_level)) changes.reorder_level = next.reorder_level
  if (!sameAmount(next.mrp, original.mrp)) changes.mrp = next.mrp
  if (!sameAmount(next.selling_price, original.selling_price)) changes.selling_price = next.selling_price
  if (!sameAmount(next.purchase_price, original.purchase_price)) changes.purchase_price = next.purchase_price
  if (next.barcode !== original.barcode) changes.barcode = next.barcode
  return changes
}

/** Maps API field names to form field names, for showing server errors next to the right input. */
export const API_FIELD_TO_FORM_FIELD: Record<string, FieldName> = {
  sku: 'sku',
  name: 'name',
  brand: 'brand',
  category_id: 'categoryId',
  unit_id: 'unitId',
  reorder_level: 'reorderLevel',
  mrp: 'mrp',
  selling_price: 'sellingPrice',
  purchase_price: 'purchasePrice',
  barcode: 'barcode',
  opening_stock: 'openingStock',
  opening_stock_cost: 'openingStockCost',
}
