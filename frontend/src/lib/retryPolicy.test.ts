import { describe, expect, it } from 'vitest'

import { ApiError } from '@/api/client'

import { isTransient, MAX_QUERY_RETRIES, MUTATIONS_RETRY, queryRetryDelay, shouldRetryQuery } from './retryPolicy'

const network = new ApiError('x', 0, {}, {}, { category: 'network', retryable: true })
const timeout = new ApiError('x', 0, {}, {}, { category: 'timeout', retryable: true })
const unavailable = new ApiError('x', 503, {}, {}, { category: 'external_api', retryable: true })
const invalid = new ApiError('x', 422, {}, {}, { category: 'validation' })
const missing = new ApiError('x', 404, {}, {}, { category: 'not_found', retryable: true })
const bug = new ApiError('x', 500, {}, {}, { category: 'unexpected', retryable: false })

describe('retry policy', () => {
  it('retries a read that failed for a reason that can pass', () => {
    for (const error of [network, timeout, unavailable]) expect(shouldRetryQuery(0, error)).toBe(true)
  })

  it('never retries a request that is itself wrong, or a bug', () => {
    for (const error of [invalid, missing, bug, new Error('boom')]) expect(shouldRetryQuery(0, error)).toBe(false)
  })

  it('stops after a couple of tries', () => {
    expect(shouldRetryQuery(MAX_QUERY_RETRIES, network)).toBe(false)
  })

  it('never retries a write by itself', () => {
    expect(MUTATIONS_RETRY).toBe(false)
  })

  it('waits a little longer each time, up to a cap', () => {
    expect(queryRetryDelay(0)).toBeLessThan(queryRetryDelay(1))
    expect(queryRetryDelay(20)).toBeLessThanOrEqual(4000)
  })

  it('treats only network, timeout and unavailable services as transient', () => {
    expect(isTransient(network)).toBe(true)
    expect(isTransient(invalid)).toBe(false)
  })
})
