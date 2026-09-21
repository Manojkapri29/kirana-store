/**
 * Offer form values, and conversion to the API payload. As with billing, there is no discount arithmetic here:
 * the server works out what each bill gets. These are only the values a person types, checked for format.
 */
import type { Promotion, PromotionAudience, PromotionPayload, PromotionScope, PromotionType } from '@/api/types'

export interface Named {
  id: number
  name: string
}

export interface PromotionFormValues {
  name: string
  description: string
  promo_type: PromotionType
  scope: PromotionScope
  percent: string
  amount: string
  offer_price: string
  buy_quantity: string
  get_quantity: string
  get_percent: string
  min_cart_value: string
  min_quantity: string
  max_discount: string
  usage_limit: string
  per_customer_limit: string
  coupon_code: string
  audience: PromotionAudience
  starts_at: string
  ends_at: string
  priority: string
  stackable: boolean
  products: Named[]
  category_ids: number[]
  customers: Named[]
}

export const emptyPromotion = (): PromotionFormValues => ({
  name: '',
  description: '',
  promo_type: 'PERCENT',
  scope: 'CART',
  percent: '',
  amount: '',
  offer_price: '',
  buy_quantity: '',
  get_quantity: '',
  get_percent: '',
  min_cart_value: '',
  min_quantity: '',
  max_discount: '',
  usage_limit: '',
  per_customer_limit: '',
  coupon_code: '',
  audience: 'ALL',
  starts_at: '',
  ends_at: '',
  priority: '0',
  stackable: false,
  products: [],
  category_ids: [],
  customers: [],
})

/** A stored UTC time as the "YYYY-MM-DDTHH:mm" a date-time input wants, in the shop's own timezone. */
export function toLocalInput(iso: string | null, timeZone: string): string {
  if (!iso) return ''
  const text = new Date(iso).toLocaleString('sv-SE', { timeZone, hour12: false })
  return text.replace(' ', 'T').slice(0, 16)
}

export function valuesFromPromotion(p: Promotion, timeZone: string): PromotionFormValues {
  return {
    name: p.name,
    description: p.description ?? '',
    promo_type: p.promo_type,
    scope: p.scope,
    percent: p.percent ?? '',
    amount: p.amount ?? '',
    offer_price: p.offer_price ?? '',
    buy_quantity: p.buy_quantity === null ? '' : String(p.buy_quantity),
    get_quantity: p.get_quantity === null ? '' : String(p.get_quantity),
    get_percent: p.get_percent && p.get_percent !== '100.00' ? p.get_percent : '',
    min_cart_value: p.min_cart_value ?? '',
    min_quantity: p.min_quantity ?? '',
    max_discount: p.max_discount ?? '',
    usage_limit: p.usage_limit === null ? '' : String(p.usage_limit),
    per_customer_limit: p.per_customer_limit === null ? '' : String(p.per_customer_limit),
    coupon_code: p.coupon_code ?? '',
    audience: p.audience,
    starts_at: toLocalInput(p.starts_at, timeZone),
    ends_at: toLocalInput(p.ends_at, timeZone),
    priority: String(p.priority),
    stackable: p.stackable,
    products: p.product_ids.map((id, i) => ({ id, name: p.products[i] ?? `#${id}` })),
    category_ids: p.category_ids,
    customers: p.customer_ids.map((id, i) => ({ id, name: p.customers[i] ?? `#${id}` })),
  }
}

const orNull = (text: string) => (text.trim() === '' ? null : text.trim())
const intOrNull = (text: string): number | null => (/^\d+$/.test(text.trim()) ? Number(text.trim()) : null)

/** Which whole-number fields are filled with something that is not a whole number. */
export function numberProblems(v: PromotionFormValues): string[] {
  const bad: string[] = []
  const whole: [string, string][] = [
    ['buy_quantity', v.buy_quantity],
    ['get_quantity', v.get_quantity],
    ['usage_limit', v.usage_limit],
    ['per_customer_limit', v.per_customer_limit],
  ]
  for (const [name, value] of whole) if (value.trim() !== '' && intOrNull(value) === null) bad.push(name)
  if (!/^-?\d+$/.test(v.priority.trim() || '0')) bad.push('priority')
  return bad
}

export function promotionPayload(v: PromotionFormValues, mode: 'create' | 'edit'): PromotionPayload {
  const payload: PromotionPayload = {
    name: v.name.trim(),
    description: orNull(v.description),
    priority: Number(v.priority.trim() || '0'),
    stackable: v.stackable,
    starts_at: orNull(v.starts_at),
    ends_at: orNull(v.ends_at),
    coupon_code: orNull(v.coupon_code),
    audience: v.audience,
    percent: v.promo_type === 'PERCENT' ? orNull(v.percent) : null,
    amount: v.promo_type === 'AMOUNT' ? orNull(v.amount) : null,
    offer_price: v.promo_type === 'OFFER_PRICE' ? orNull(v.offer_price) : null,
    buy_quantity: v.promo_type === 'BUY_X_GET_Y' ? intOrNull(v.buy_quantity) : null,
    get_quantity: v.promo_type === 'BUY_X_GET_Y' ? intOrNull(v.get_quantity) : null,
    get_percent: v.promo_type === 'BUY_X_GET_Y' ? orNull(v.get_percent) : null,
    min_cart_value: orNull(v.min_cart_value),
    min_quantity: orNull(v.min_quantity),
    max_discount: orNull(v.max_discount),
    usage_limit: intOrNull(v.usage_limit),
    per_customer_limit: intOrNull(v.per_customer_limit),
    product_ids: v.scope === 'PRODUCTS' ? v.products.map((p) => p.id) : [],
    category_ids: v.scope === 'CATEGORIES' ? v.category_ids : [],
    customer_ids: v.audience === 'CUSTOMERS' ? v.customers.map((c) => c.id) : [],
  }
  // The kind and scope are chosen once, when the offer is created.
  return mode === 'create' ? { ...payload, promo_type: v.promo_type, scope: v.scope } : payload
}
