import type { BalanceStatus } from '@/api/types'

/** The meaning of a signed balance string: positive = owes, zero = settled, negative = advance. */
export const statusOf = (balance: string): BalanceStatus =>
  balance.startsWith('-') ? 'ADVANCE' : /^0+(\.0+)?$/.test(balance) ? 'SETTLED' : 'OUTSTANDING'
