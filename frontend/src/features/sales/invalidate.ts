import type { QueryClient } from '@tanstack/react-query'

/**
 * Posting or voiding a sale changes stock, and possibly a customer's balance, so every screen that shows
 * either must refetch, not only the sale screens.
 */
export function invalidateSaleData(queryClient: QueryClient): Promise<unknown> {
  const keys = ['sales', 'sale', 'products', 'product', 'inventory', 'history', 'customers', 'customer', 'customerLedger']
  return Promise.all(keys.map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
}
