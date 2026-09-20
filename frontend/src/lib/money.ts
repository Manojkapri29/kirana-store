/**
 * Exact money and quantity arithmetic for the purchase form, using whole numbers only (BigInt).
 *
 * Money is counted in paise (1/100) and quantities in thousandths (1/1000). No JavaScript floating point is
 * involved anywhere, so a line total shown on screen is exactly the one the server will calculate.
 * The rule mirrors the backend: line = round_half_up(quantity x price) - discount.
 */

const parseScaled = (text: string, places: number): bigint | null => {
  const value = text.trim()
  if (!/^\d+(\.\d+)?$/.test(value)) return null
  const [whole, fraction = ''] = value.split('.')
  const kept = fraction.replace(/0+$/, '')
  if (kept.length > places) return null
  return BigInt(whole + kept.padEnd(places, '0'))
}

/** "12.5" -> 1250n paise. `null` when the text is not a valid amount with at most 2 decimals. */
export const toPaise = (text: string): bigint | null => parseScaled(text, 2)

/** "2.5" -> 2500n thousandths. `null` when invalid or with more than 3 decimals. */
export const toThousandths = (text: string): bigint | null => parseScaled(text, 3)

/** 123456n -> "1234.56". Always two decimals, no grouping (formatMoney adds the display styling). */
export function paiseToText(paise: bigint): string {
  const negative = paise < 0n
  const absolute = negative ? -paise : paise
  const whole = absolute / 100n
  const fraction = (absolute % 100n).toString().padStart(2, '0')
  return `${negative ? '-' : ''}${whole}.${fraction}`
}

/** quantity (thousandths) x price (paise), rounded half up to whole paise. */
export const grossPaise = (quantity: bigint, price: bigint): bigint => (quantity * price + 500n) / 1000n

export interface LineAmounts {
  gross: bigint
  discount: bigint
  total: bigint
}

/**
 * The amounts of one purchase line, or `null` if any input is missing or invalid, or the discount is more
 * than the line is worth (the server refuses that too).
 */
export function lineAmounts(quantity: string, price: string, discount: string): LineAmounts | null {
  const q = toThousandths(quantity)
  const p = toPaise(price)
  const d = discount.trim() === '' ? 0n : toPaise(discount)
  if (q === null || p === null || d === null || q === 0n) return null
  const gross = grossPaise(q, p)
  if (d > gross) return null
  return { gross, discount: d, total: gross - d }
}
