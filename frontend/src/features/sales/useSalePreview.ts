import { keepPreviousData, useQuery } from '@tanstack/react-query'

import { calculateSale } from '@/api/sales'
import type { SaleItemPayload } from '@/api/types'
import { useDebounced } from '@/hooks/useDebounced'

export interface PreviewContext {
  /** Some offers depend on the customer. */
  customerId: number | null
  /** The coupon code the cashier applied, if any. */
  couponCode: string | null
}

/**
 * The server's pricing of a cart (`/sales/calculate`), including any offers and coupon. Everything the screen
 * shows for totals comes from here, so there is one implementation of the arithmetic. Re-asked shortly after the
 * cart stops changing; the previous answer stays on screen meanwhile.
 */
export function useSalePreview(
  items: SaleItemPayload[],
  discount: string | null,
  amountPaid: string | null,
  context: PreviewContext = { customerId: null, couponCode: null },
) {
  const request = useDebounced(
    JSON.stringify({
      items,
      discount,
      amount_paid: amountPaid,
      customer_id: context.customerId,
      coupon_code: context.couponCode,
    }),
    250,
  )
  return useQuery({
    queryKey: ['saleCalc', request],
    queryFn: () => calculateSale(JSON.parse(request) as Parameters<typeof calculateSale>[0]),
    placeholderData: keepPreviousData,
    enabled: items.length > 0,
  })
}
