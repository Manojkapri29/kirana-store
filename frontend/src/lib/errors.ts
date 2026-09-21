/**
 * Turns any failure into what a person should see and do. Pure, so it is easy to test.
 *
 * Rules (docs/BUSINESS_RULES.md, ER):
 *  - a person never sees technical text: not a server message for an unexpected failure, not a status code, not a
 *    stack. They see a plain sentence, a reference to quote (only when the server made one), and what they can do;
 *  - errors are not hidden: the reference lets support find the real cause in the server's log;
 *  - not everything is recoverable: "Try again" is offered only when trying again could help AND is safe;
 *  - what the person typed is never thrown away: forms keep their state and say so.
 */

import type { ParseKeys } from 'i18next'

import { ApiError, type ErrorCategory } from '@/api/client'

export type ErrorContext = 'ai' | 'generic' | 'save' | 'checkout' | 'barcode' | 'price' | 'image_upload' | 'search' | 'return'

export type RecoveryAction =
  | 'retry'
  | 'save_draft'
  | 'go_back'
  | 'refresh'
  | 'continue'
  | 'manual_entry'
  | 'choose_another'
  | 'adjust_cart'
  | 'cancel'
  | 'contact_support'

export interface Recovery {
  category: ErrorCategory
  titleKey: ParseKeys
  /** A message from the server that is meant for people (a validation or business rule). Never for unexpected errors. */
  serverMessage: string | null
  messageKey: ParseKeys | null
  /** Say that what was entered is still on the screen. */
  preserved: boolean
  reference: string | null
  actions: RecoveryAction[]
  /** True for a problem the person can fix by changing what they entered. */
  needsInputChange: boolean
  /** Seconds the server asked the person to wait (rate limiting). Null when it did not say. */
  waitSeconds: number | null
}

const TRANSIENT: ErrorCategory[] = ['network', 'timeout', 'database', 'external_api']
const INTERNAL: ErrorCategory[] = [...TRANSIENT, 'unexpected', 'checkout', 'promotion_calculation', 'image_upload']
const WRITING: ErrorContext[] = ['save', 'checkout', 'return']

const TITLES: Record<ErrorContext, ParseKeys> = {
  ai: 'recovery.titles.ai',
  generic: 'recovery.titles.generic',
  save: 'recovery.titles.save',
  checkout: 'recovery.titles.checkout',
  barcode: 'recovery.titles.barcode',
  price: 'recovery.titles.price',
  image_upload: 'recovery.titles.image_upload',
  search: 'recovery.titles.search',
  return: 'recovery.titles.return',
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  // Anything that is not one of ours (a bug in the screen): treat it as unexpected, and show nothing of it.
  return new ApiError('unexpected', 500, {}, {}, { category: 'unexpected' })
}

export interface DescribeOptions {
  context?: ErrorContext
  /** Is repeating this exact request safe? True for reads, and for writes that carry an idempotency key. */
  safeToRepeat?: boolean
}

export function describeError(input: unknown, options: DescribeOptions = {}): Recovery {
  const error = toApiError(input)
  const context = options.context ?? 'generic'
  const category = error.category
  const writing = WRITING.includes(context)
  const internal = INTERNAL.includes(category)
  const transient = TRANSIENT.includes(category) || error.retryable
  const safe = options.safeToRepeat ?? !writing
  // Reading again is harmless, so a failed read can always be retried; a failed write only when it is known to
  // be transient AND safe to repeat (it carries an idempotency key).
  const canRetry = safe && (transient || (!writing && internal))

  const base = {
    category,
    reference: error.referenceId,
    preserved: writing || context === 'image_upload',
    needsInputChange: false,
    serverMessage: internal ? null : error.message,
    waitSeconds: error.retryAfterSeconds,
  }

  // Too many requests: nothing is wrong with the data. Wait, then it is safe to repeat a read; a write is repeated
  // by the person (with its idempotency key) once the wait is over.
  if (category === 'rate_limited') {
    return { ...base, titleKey: 'recovery.titles.rateLimited', messageKey: null, actions: safe ? ['retry', 'go_back'] : ['go_back'] }
  }
  // The shop's account state does not allow this. The server's sentence is written for the owner.
  if (category === 'account_restricted') {
    return { ...base, titleKey: 'recovery.titles.accountRestricted', messageKey: null, actions: ['go_back', 'contact_support'] }
  }

  // A stock problem while checking out: show what is available and let the cart be adjusted.
  if (category === 'insufficient_stock' || category === 'inventory_conflict') {
    return {
      ...base,
      titleKey: 'recovery.titles.stock',
      messageKey: null,
      actions: context === 'checkout' ? ['adjust_cart', 'go_back'] : ['go_back'],
      needsInputChange: true,
    }
  }
  if (category === 'validation') {
    return { ...base, titleKey: 'recovery.titles.validation', messageKey: null, actions: [], needsInputChange: true }
  }
  if (category === 'duplicate') {
    return { ...base, titleKey: 'recovery.titles.duplicate', messageKey: null, actions: ['go_back'] }
  }
  if (category === 'not_found') {
    return { ...base, titleKey: 'recovery.titles.notFound', messageKey: null, actions: ['go_back', 'refresh'] }
  }
  if (category === 'authentication') {
    return { ...base, titleKey: 'recovery.titles.authentication', messageKey: null, actions: ['refresh'] }
  }
  if (category === 'authorization' || category === 'plan_limit') {
    return {
      ...base,
      titleKey: category === 'plan_limit' ? 'recovery.titles.planLimit' : 'recovery.titles.authorization',
      messageKey: null,
      actions: ['go_back'],
    }
  }
  if (category === 'conflict') {
    return { ...base, titleKey: 'recovery.titles.conflict', messageKey: null, actions: ['refresh', 'go_back'] }
  }

  // The rest are failures on our side or in between. The person gets a plain sentence, never the cause.
  const messageKey: ParseKeys = `recovery.messages.${category}` as ParseKeys
  const actions: RecoveryAction[] = []
  if (context === 'barcode') actions.push('manual_entry')
  if (context === 'image_upload') actions.push('choose_another')
  if (context === 'price' || category === 'promotion_calculation') actions.push('continue')
  if (canRetry) actions.unshift('retry')
  if (context === 'checkout' || (context === 'save' && !canRetry)) actions.push('save_draft')
  if (writing) actions.push(context === 'save' ? 'cancel' : 'go_back')
  if (!actions.includes('go_back') && !actions.includes('cancel') && !actions.includes('continue')) actions.push('refresh')
  // Something failed on our side and there is a reference to quote: the person can hand it to support.
  if (base.reference && internal) actions.push('contact_support')
  return { ...base, titleKey: TITLES[context], messageKey, actions: [...new Set(actions)] }
}

/** Does this message contain anything that must never reach a person? (Used by tests as a safety net.) */
export const TECHNICAL_LEAKS = [
  'Traceback',
  'sqlalchemy',
  'sqlite',
  'psycopg',
  'fastapi',
  'site-packages',
  'Internal Server Error',
  'IntegrityError',
  'OperationalError',
  '/Users/',
  'at Object.',
  'undefined',
  '[object Object]',
]
