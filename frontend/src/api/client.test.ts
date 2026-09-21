import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch, apiSend } from './client'

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the API client', () => {
  it('returns the parsed body of a good answer, and nothing for an empty 204', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(respond(200, { ok: true })).mockResolvedValueOnce(new Response(null, { status: 204 })))
    expect(await apiFetch('/x')).toEqual({ ok: true })
    expect(await apiFetch('/x')).toBeUndefined()
  })

  it('reads the standard error body: category, code, reference id and retryable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        respond(500, {
          success: false,
          error_code: 'unexpected_error',
          message: 'Something went wrong while completing this action.',
          category: 'unexpected',
          retryable: false,
          reference_id: 'ERR-20260921-A82F5',
        }),
      ),
    )
    const error = await apiFetch('/x').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    const api = error as ApiError
    expect(api.category).toBe('unexpected')
    expect(api.referenceId).toBe('ERR-20260921-A82F5')
    expect(api.errorCode).toBe('unexpected_error')
    expect(api.retryable).toBe(false)
    expect(api.message).toBe('Something went wrong while completing this action.')
  })

  it('keeps field messages of a validation error, and its category', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        respond(422, { detail: [{ loc: ['body', 'items', 0, 'quantity'], msg: 'Enter a number', type: 'value_error' }], category: 'validation' }),
      ),
    )
    const error = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError
    expect(error.category).toBe('validation')
    expect(error.fieldErrors).toEqual({ quantity: 'Enter a number' })
    expect(error.pathErrors).toEqual({ 'items.0.quantity': 'Enter a number' })
  })

  it('turns a lost connection into a retryable network error with no technical text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const error = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError
    expect(error.category).toBe('network')
    expect(error.retryable).toBe(true)
    expect(error.message).not.toContain('Failed to fetch')
  })

  it('gives up on a slow server and reports a timeout', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))),
    )
    const pending = apiFetch('/slow', { timeoutMs: 1000 }).catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(1001)
    const error = (await pending) as ApiError
    expect(error.category).toBe('timeout')
    expect(error.retryable).toBe(true)
  })

  it('does not mistake a non-JSON error page for a message it can show', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Internal Server Error /Users/x/app.py</html>', { status: 502 })))
    const error = (await apiFetch('/x').catch((e: unknown) => e)) as ApiError
    expect(error.message).not.toContain('/Users/')
    expect(error.retryable).toBe(true)
  })

  it('sends an idempotency key only when one is given', async () => {
    const fetchMock = vi.fn((_url: string, _init?: RequestInit) => Promise.resolve(respond(200, {})))
    vi.stubGlobal('fetch', fetchMock)
    await apiSend('POST', '/sales', { a: 1 }, { idempotencyKey: 'attempt-123' })
    await apiSend('POST', '/sales', { a: 1 })
    const first = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers)
    const second = new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers)
    expect(first.get('Idempotency-Key')).toBe('attempt-123')
    expect(second.get('Idempotency-Key')).toBeNull()
  })
})
