import type { QueryClient } from '@tanstack/react-query'

/**
 * Posting or voiding a purchase changes stock and average cost, so every screen that shows either must
 * refetch, not only the purchase screens.
 */
export function invalidatePurchaseData(queryClient: QueryClient): Promise<unknown> {
  const keys = [
    'purchases',
    'purchase',
    'supplierPurchaseTotals',
    'products',
    'product',
    'inventory',
    'history',
  ]
  return Promise.all(keys.map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
}
