import { useQuery } from '@tanstack/react-query'

import { getSubscription } from '@/api/subscription'

export type Feature = 'barcode_lookup' | 'promotions' | 'price_intelligence' | 'advanced_reports' | 'online_store' | 'image_intelligence'

/**
 * What the shop's plan includes. This is only for showing or hiding things: the server enforces the plan on every
 * request, so a screen that guesses wrong just gets a clear "not part of your plan" answer.
 * `allows` is `undefined` until the plan is known, so nothing flickers away while it loads.
 */
export function useEntitlements() {
  const query = useQuery({ queryKey: ['subscription'], queryFn: getSubscription, staleTime: 60_000 })
  const features = query.data?.features
  return {
    subscription: query.data,
    allows: (feature: Feature): boolean | undefined => (features ? Boolean(features[feature]) : undefined),
  }
}
