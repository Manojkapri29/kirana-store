import { useState } from 'react'

import { ApiError, type ErrorCategory } from '@/api/client'

/** Failures a person cannot fix by editing a field: they get the recovery notice instead of a field message. */
const NOTICE: ErrorCategory[] = [
  'network',
  'timeout',
  'database',
  'external_api',
  'unexpected',
  'checkout',
  'promotion_calculation',
  'image_upload',
  'insufficient_stock',
  'inventory_conflict',
  'authentication',
]

export function needsNotice(error: unknown): boolean {
  return !(error instanceof ApiError) || NOTICE.includes(error.category)
}

/** The last failure of a save that is shown as a recovery notice. Cleared when the person tries again. */
export function useFailure() {
  const [failure, setFailure] = useState<unknown>(null)
  return { failure, setFailure, clear: () => setFailure(null), has: failure !== null }
}
