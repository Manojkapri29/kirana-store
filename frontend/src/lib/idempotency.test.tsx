import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { newIdempotencyKey, useIdempotencyKey } from './idempotency'

describe('idempotency keys', () => {
  it('makes a fresh, well-formed key each time', () => {
    const a = newIdempotencyKey()
    expect(a).toMatch(/^[A-Za-z0-9._:-]{8,100}$/) // the shape the server accepts
    expect(newIdempotencyKey()).not.toBe(a)
  })

  it('keeps the same key for a retry of the same request', () => {
    const { result } = renderHook(() => useIdempotencyKey())
    const first = result.current.keyFor('{"total":100}')
    expect(result.current.keyFor('{"total":100}')).toBe(first)
  })

  it('uses a new key when what is being sent has changed', () => {
    const { result } = renderHook(() => useIdempotencyKey())
    const first = result.current.keyFor('{"total":100}')
    expect(result.current.keyFor('{"total":150}')).not.toBe(first)
  })

  it('forgets the key after a success so the next sale is a new one', () => {
    const { result } = renderHook(() => useIdempotencyKey())
    const first = result.current.keyFor('same')
    result.current.renew()
    expect(result.current.keyFor('same')).not.toBe(first)
  })
})
