import { API_V1_PREFIX, apiFetch } from './client'
import type { Subscription } from './types'

/** The shop's plan, what it includes, its limits and this month's usage. Read-only: plans are changed by an admin. */
export const getSubscription = () => apiFetch<Subscription>(`${API_V1_PREFIX}/subscription`)
