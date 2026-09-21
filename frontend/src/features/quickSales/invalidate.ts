import type { QueryClient } from '@tanstack/react-query'

/** A quick sale can put credit on a customer's khata, so the customer screens must refetch too. */
export function invalidateQuickSaleData(queryClient: QueryClient): Promise<unknown> {
  const keys = ['quickSales', 'quickSale', 'customers', 'customer', 'customerLedger', 'salesSummary', 'subscription']
  return Promise.all(keys.map((key) => queryClient.invalidateQueries({ queryKey: [key] })))
}
