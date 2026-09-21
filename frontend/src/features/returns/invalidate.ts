import type { QueryClient } from '@tanstack/react-query'

/**
 * A return (or its void) changes stock, a customer's balance, the sales figures and the sale or purchase it came
 * from, so everything that shows any of those must refetch.
 */
export function invalidateReturnData(queryClient: QueryClient): Promise<unknown> {
  const keys = [
    'sales', 'sale', 'purchases', 'purchase', 'salesReturns', 'salesReturn', 'purchaseReturns', 'purchaseReturn',
    'products', 'product', 'inventory', 'history', 'customers', 'customer', 'customerLedger', 'salesSummary', 'discountReport',
  ]
  return Promise.all(keys.map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
}
