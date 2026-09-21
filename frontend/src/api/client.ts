/** Prefix for all versioned business endpoints (products, inventory, ...). */
export const API_V1_PREFIX = '/api/v1'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

interface ValidationDetail {
  loc: (string | number)[]
  msg: string
  type: string
}

/** What kind of problem this was. It decides the recovery on screen (see `lib/errors.ts`). */
export type ErrorCategory =
  | 'validation'
  | 'not_found'
  | 'conflict'
  | 'duplicate'
  | 'insufficient_stock'
  | 'inventory_conflict'
  | 'authentication'
  | 'authorization'
  | 'plan_limit'
  | 'network'
  | 'external_api'
  | 'timeout'
  | 'database'
  | 'image_upload'
  | 'promotion_calculation'
  | 'checkout'
  | 'rate_limited'
  | 'account_restricted'
  | 'unexpected'

export interface ErrorMeta {
  category?: ErrorCategory
  errorCode?: string | null
  /** Set only for unexpected failures. It is what a person quotes; the real cause is in the server's log. */
  referenceId?: string | null
  /** Could trying the same request again succeed? (Not the same as "safe to repeat": see `lib/retryPolicy.ts`.) */
  retryable?: boolean
  /** Structured facts from the server, for example the possible duplicates. */
  data?: unknown
  /** Seconds to wait before trying again, when the server asked (HTTP 429 sends Retry-After). */
  retryAfterSeconds?: number | null
}

export class ApiError extends Error {
  readonly status: number
  /** Messages keyed by form field name, when the server says which input a problem belongs to. */
  readonly fieldErrors: Record<string, string>
  /** The same messages keyed by full path, e.g. "items.2.quantity", for problems inside a list. */
  readonly pathErrors: Record<string, string>
  readonly category: ErrorCategory
  readonly errorCode: string | null
  readonly referenceId: string | null
  readonly retryable: boolean
  readonly data: unknown
  readonly retryAfterSeconds: number | null

  constructor(
    message: string,
    status: number,
    fieldErrors: Record<string, string> = {},
    pathErrors: Record<string, string> = {},
    meta: ErrorMeta = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
    this.pathErrors = pathErrors
    this.category = meta.category ?? categoryFromStatus(status)
    this.errorCode = meta.errorCode ?? null
    this.referenceId = meta.referenceId ?? null
    this.retryable = meta.retryable ?? false
    this.data = meta.data
    this.retryAfterSeconds = meta.retryAfterSeconds ?? null
  }
}

function categoryFromStatus(status: number): ErrorCategory {
  if (status === 0) return 'network'
  if (status === 401) return 'authentication'
  if (status === 403) return 'authorization'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 422) return 'validation'
  if (status === 429) return 'rate_limited'
  if (status === 504) return 'timeout'
  return status >= 500 ? 'unexpected' : 'validation'
}

interface ErrorBody {
  success?: boolean
  error_code?: string | null
  message?: string
  category?: ErrorCategory
  retryable?: boolean
  reference_id?: string | null
  detail?: unknown
  data?: unknown
}

function parseRetryAfter(value: string | null): number | null {
  const seconds = value === null ? NaN : Number(value)
  return Number.isFinite(seconds) && seconds >= 0 ? Math.min(Math.ceil(seconds), 3600) : null
}

async function toApiError(response: Response, path: string): Promise<ApiError> {
  let body: ErrorBody = {}
  try {
    body = (await response.json()) as ErrorBody
  } catch {
    // Not JSON (for example a proxy error page): fall through to the generic message.
  }
  const meta: ErrorMeta = {
    category: body.category,
    errorCode: body.error_code ?? null,
    referenceId: body.reference_id ?? null,
    retryable: body.retryable ?? response.status >= 502,
    data: body.data,
    retryAfterSeconds: parseRetryAfter(response.headers.get('Retry-After')),
  }
  const detail = body.detail

  // An unexpected failure: show only the server's plain message. The technical text stays on the server.
  if (meta.referenceId) return new ApiError(body.message ?? 'Something went wrong.', response.status, {}, {}, meta)
  if (typeof detail === 'string') return new ApiError(detail, response.status, {}, {}, meta)

  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {}
    const pathErrors: Record<string, string> = {}
    const messages: string[] = []
    for (const item of detail as ValidationDetail[]) {
      const field = item.loc?.length > 1 ? String(item.loc[item.loc.length - 1]) : undefined
      // Show only the first message per field.
      if (field && !(field in fieldErrors)) fieldErrors[field] = item.msg
      const path = item.loc?.length > 1 ? item.loc.slice(1).join('.') : undefined
      if (path && !(path in pathErrors)) pathErrors[path] = item.msg
      messages.push(item.msg)
    }
    return new ApiError(
      messages[0] ?? body.message ?? `Request failed (${response.status})`,
      response.status,
      fieldErrors,
      pathErrors,
      meta,
    )
  }

  return new ApiError(body.message ?? `Request to ${path} failed with status ${response.status}`, response.status, {}, {}, meta)
}

function url(path: string, query?: Record<string, string | number | boolean | null | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const queryString = params.toString()
  return `${BASE_URL}${path}${queryString ? `?${queryString}` : ''}`
}

export type Query = Record<string, string | number | boolean | null | undefined>

/** How long a request may take before it is given up (a slow server must not freeze a screen). */
export const DEFAULT_TIMEOUT_MS = 30_000

export interface SendOptions {
  timeoutMs?: number
  /** Makes a repeat of this request safe: the server does the work once and returns the same answer. */
  idempotencyKey?: string
}

/** Fired when the server says the session is no longer valid, so the app can ask the person to sign in again in place. */
export const UNAUTHORIZED_EVENT = 'kirana:unauthorized'
/** Fired after each API call, so the app can tell how long the person has been idle (for the session-expiry warning). */
export const ACTIVITY_EVENT = 'kirana:activity'
const CSRF_COOKIE = 'kirana_csrf'
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

/** The CSRF token the server put in a cookie the app can read. It is sent back in a header on every change. */
function csrfToken(): string | null {
  try {
    const found = document.cookie.split('; ').find((entry) => entry.startsWith(`${CSRF_COOKIE}=`))
    return found ? decodeURIComponent(found.slice(CSRF_COOKIE.length + 1)) : null
  } catch {
    return null
  }
}

/** One fetch with a timeout. Turns a lost connection or a timeout into an `ApiError` the screens can explain. */
async function request(path: string, init: RequestInit, options: SendOptions = {}): Promise<Response> {
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, options.timeoutMs ?? DEFAULT_TIMEOUT_MS)
  try {
    const headers = new Headers(init.headers)
    if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey)
    const method = (init.method ?? 'GET').toUpperCase()
    const token = UNSAFE.has(method) ? csrfToken() : null
    if (token && !headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', token)
    // Same-origin in development (the Vite proxy) and behind a reverse proxy; an API on another origin needs "include".
    const response = await fetch(path, { ...init, headers, signal: controller.signal, credentials: BASE_URL ? 'include' : 'same-origin' })
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new Event(ACTIVITY_EVENT))
      if (response.status === 401 && !path.includes('/auth/')) {
        window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
      }
    }
    return response
  } catch {
    if (timedOut) {
      throw new ApiError('This is taking longer than expected.', 0, {}, {}, { category: 'timeout', retryable: true })
    }
    throw new ApiError('The connection was interrupted.', 0, {}, {}, { category: 'network', retryable: true })
  } finally {
    clearTimeout(timer)
  }
}

/**
 * The single place where the app talks to the backend over HTTP.
 * Feature modules add small typed functions on top of this; React components never call `fetch`.
 * Money and quantities travel as strings ("25.50"); they are never converted to JavaScript numbers.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit & { query?: Query } & SendOptions = {},
): Promise<T> {
  const { query, timeoutMs, idempotencyKey, ...rest } = init
  const response = await request(
    url(path, query),
    { ...rest, headers: { Accept: 'application/json', ...(rest.headers as Record<string, string>) } },
    { timeoutMs, idempotencyKey },
  )
  if (!response.ok) throw await toApiError(response, path)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function apiSend<T>(
  method: 'POST' | 'PATCH' | 'PUT',
  path: string,
  body?: unknown,
  options: SendOptions = {},
): Promise<T> {
  return apiFetch<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...options,
  })
}

export interface Download {
  blob: Blob
  filename: string
}

export async function apiDownload(path: string, query?: Query): Promise<Download> {
  const response = await request(url(path, query), {})
  if (!response.ok) throw await toApiError(response, path)
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const filename = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? 'export'
  return { blob: await response.blob(), filename }
}

/** A private file (a kept photo). Fetched through the API so it is never a public address. */
export async function apiBlob(path: string): Promise<Blob> {
  const response = await request(url(path), {})
  if (!response.ok) throw await toApiError(response, path)
  return response.blob()
}
