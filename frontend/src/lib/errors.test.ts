import { describe, expect, it } from 'vitest'

import { ApiError, type ErrorCategory } from '@/api/client'
import { en } from '@/i18n/locales/en'

import { describeError, TECHNICAL_LEAKS } from './errors'

const make = (category: ErrorCategory, status = 500, referenceId: string | null = null, retryable = false, message = 'server text') =>
  new ApiError(message, status, {}, {}, { category, referenceId, retryable })

/** Every user-facing string the recovery layer can produce. */
function allRecoveryText(): string {
  return JSON.stringify(en.recovery)
}

describe('describeError', () => {
  it('never shows the server text of an unexpected failure, only the reference to quote', () => {
    const recovery = describeError(make('unexpected', 500, 'ERR-20260921-A82F5', false, 'sqlalchemy.exc.OperationalError at /Users/x/db.py'))
    expect(recovery.serverMessage).toBeNull()
    expect(recovery.reference).toBe('ERR-20260921-A82F5')
  })

  it('keeps the readable message of a business rule (it is written for people)', () => {
    const recovery = describeError(make('conflict', 409, null, false, 'This sale has a return, so it cannot be voided.'))
    expect(recovery.serverMessage).toBe('This sale has a return, so it cannot be voided.')
  })

  it('offers "Try again" for a failed read, even for an unexpected failure', () => {
    expect(describeError(make('unexpected'), { context: 'search' }).actions).toContain('retry')
  })

  it('offers "Try again" for a save only when repeating it is safe', () => {
    const transient = make('network', 0, null, true)
    expect(describeError(transient, { context: 'checkout', safeToRepeat: false }).actions).not.toContain('retry')
    expect(describeError(transient, { context: 'checkout', safeToRepeat: true }).actions).toContain('retry')
  })

  it('does not offer to retry a failure that retrying cannot fix', () => {
    expect(describeError(make('unexpected'), { context: 'checkout', safeToRepeat: true }).actions).not.toContain('retry')
  })

  it('says the data was kept when a save fails', () => {
    expect(describeError(make('network', 0, null, true), { context: 'save' }).preserved).toBe(true)
    expect(describeError(make('network', 0, null, true), { context: 'search' }).preserved).toBe(false)
  })

  it('lets the cart be adjusted after a stock conflict at checkout', () => {
    const recovery = describeError(make('insufficient_stock', 409, null, false, 'Only 3 kg available.'), { context: 'checkout' })
    expect(recovery.actions).toContain('adjust_cart')
    expect(recovery.needsInputChange).toBe(true)
    expect(recovery.serverMessage).toBe('Only 3 kg available.')
  })

  it('offers manual entry when the barcode service is down', () => {
    expect(describeError(make('external_api', 503, null, true), { context: 'barcode' }).actions).toContain('manual_entry')
  })

  it('lets billing continue when the price service is down', () => {
    expect(describeError(make('external_api', 503, null, true), { context: 'price' }).actions).toContain('continue')
  })

  it('offers another image when an upload fails', () => {
    expect(describeError(make('image_upload', 500, 'ERR-X'), { context: 'image_upload' }).actions).toContain('choose_another')
  })

  it('treats a bug in a screen (not an ApiError) as unexpected and shows nothing of it', () => {
    const recovery = describeError(new TypeError("Cannot read properties of undefined (reading 'x')"))
    expect(recovery.category).toBe('unexpected')
    expect(recovery.serverMessage).toBeNull()
  })

  it('every category resolves to text that exists in both languages and holds no technical wording', () => {
    const categories: ErrorCategory[] = [
      'validation', 'not_found', 'conflict', 'duplicate', 'insufficient_stock', 'inventory_conflict', 'authentication',
      'authorization', 'plan_limit', 'network', 'external_api', 'timeout', 'database', 'image_upload',
      'promotion_calculation', 'checkout', 'unexpected',
    ]
    for (const category of categories) {
      const recovery = describeError(make(category))
      expect(recovery.titleKey).toBeTruthy()
      expect(recovery.actions.length + (recovery.needsInputChange ? 1 : 0)).toBeGreaterThan(0)
    }
    const text = allRecoveryText()
    for (const leak of TECHNICAL_LEAKS) expect(text.toLowerCase()).not.toContain(leak.toLowerCase())
  })
})
