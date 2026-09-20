/**
 * Validation and comparison of decimal amounts typed by the user.
 *
 * Amounts stay strings all the way to the API, so no binary floating point is involved. These helpers
 * only decide whether the text is acceptable *before* sending; the server validates again.
 */

export type DecimalCheck = 'ok' | 'empty' | 'invalid' | 'tooManyDecimals'

/** Digits with an optional fractional part. No sign, no exponent, no thousands separators. */
const DECIMAL_PATTERN = /^\d+(\.\d+)?$/

export function checkDecimal(text: string, maxDecimals: number): DecimalCheck {
  const value = text.trim()
  if (value === '') return 'empty'
  if (!DECIMAL_PATTERN.test(value)) return 'invalid'
  const decimals = value.split('.')[1]?.replace(/0+$/, '').length ?? 0 // "2.50" has 1 real decimal
  return decimals > maxDecimals ? 'tooManyDecimals' : 'ok'
}

export const isWhole = (text: string): boolean => !text.includes('.') || /^\d+\.0*$/.test(text.trim())

export function isZero(text: string): boolean {
  return /^0+(\.0*)?$/.test(text.trim())
}

/** Compare two valid non-negative decimal strings exactly (up to 3 decimals). */
export function decimalGreaterThan(a: string, b: string): boolean {
  const scale = (value: string): bigint => {
    const [whole, fraction = ''] = value.trim().split('.')
    return BigInt(whole + fraction.padEnd(3, '0').slice(0, 3))
  }
  return scale(a) > scale(b)
}
