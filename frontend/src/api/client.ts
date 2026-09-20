/** Prefix for all versioned business endpoints (products, inventory, ...). */
export const API_V1_PREFIX = '/api/v1'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

interface ValidationDetail {
  loc: (string | number)[]
  msg: string
  type: string
}

export class ApiError extends Error {
  readonly status: number
  /** Messages keyed by form field name, when the server says which input a problem belongs to. */
  readonly fieldErrors: Record<string, string>
  /** The same messages keyed by full path, e.g. "items.2.quantity", for problems inside a list. */
  readonly pathErrors: Record<string, string>

  constructor(
    message: string,
    status: number,
    fieldErrors: Record<string, string> = {},
    pathErrors: Record<string, string> = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
    this.pathErrors = pathErrors
  }
}

async function toApiError(response: Response, path: string): Promise<ApiError> {
  let detail: unknown
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail
  } catch {
    // Not JSON (for example a proxy error page): fall through to the generic message.
  }

  if (typeof detail === 'string') return new ApiError(detail, response.status)

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
      messages[0] ?? `Request failed (${response.status})`,
      response.status,
      fieldErrors,
      pathErrors,
    )
  }

  return new ApiError(`Request to ${path} failed with status ${response.status}`, response.status)
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

/**
 * The single place where the app talks to the backend over HTTP.
 * Feature modules add small typed functions on top of this; React components never call `fetch`.
 * Money and quantities travel as strings ("25.50"); they are never converted to JavaScript numbers.
 */
export async function apiFetch<T>(
  path: string,
  init: RequestInit & { query?: Query } = {},
): Promise<T> {
  const { query, ...rest } = init
  const response = await fetch(url(path, query), {
    ...rest,
    headers: { Accept: 'application/json', ...rest.headers },
  })
  if (!response.ok) throw await toApiError(response, path)
  return (await response.json()) as T
}

export function apiSend<T>(method: 'POST' | 'PATCH' | 'PUT', path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export interface Download {
  blob: Blob
  filename: string
}

export async function apiDownload(path: string, query?: Query): Promise<Download> {
  const response = await fetch(url(path, query))
  if (!response.ok) throw await toApiError(response, path)
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const filename = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? 'export'
  return { blob: await response.blob(), filename }
}
