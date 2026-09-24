import 'fake-indexeddb/auto'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import indexHtml from '../index.html?raw'
import manifestText from '../public/manifest.webmanifest?raw'
import swText from '../public/sw.js?raw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext, type AuthValue } from '@/features/auth/authContext'
import { OfflinePage } from '@/features/offline/OfflinePage'
import { signOutCleanup } from '@/offline/cleanup'
import { allDatabaseNames, dbName, deleteAllScopes, deleteScope, openDb, type Scope } from '@/offline/db'
import { enqueue, listQueue, updateOp } from '@/offline/queue'
import { findByCode, readSnapshot, refreshSnapshots, type SnapshotProduct } from '@/offline/snapshots'
import { discardOp, retryConflict, syncQueue } from '@/offline/sync'
import { en } from '@/i18n/locales/en'
import { hi } from '@/i18n/locales/hi'

const A: Scope = { shopId: 1, userId: 10 }
const B: Scope = { shopId: 2, userId: 20 }

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}
type Handler = (init?: RequestInit) => Promise<Response>
function routes(map: Record<string, Handler>) {
  const mock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split('?')[0]
    const handler = map[`${init?.method ?? 'GET'} ${path}`]
    return handler ? handler(init) : json(404, { message: 'not found' })
  })
  vi.stubGlobal('fetch', mock)
  return mock
}
const bodyOf = (init?: RequestInit) => JSON.parse(String(init?.body)) as { operations: { client_op_id: string; type: string; payload: Record<string, unknown> }[]; device_id: string }
const result = (id: string, status: string, extra: Record<string, unknown> = {}) => ({ client_op_id: id, status, result: null, error_code: null, message: null, duplicate: false, ...extra })

beforeEach(async () => {
  await deleteAllScopes()
})
afterEach(() => vi.unstubAllGlobals())

const quick = (scope: Scope, amount = '80.00') => enqueue(scope, { type: 'QUICK_SALE', label: 'Quick sale', total: amount, payload: { create: { gross_amount: amount }, payment: { payment_method: 'CASH' }, expected_total: amount } })

describe('the offline queue', () => {
  it('gives every operation its own id once and keeps them oldest first', async () => {
    const a = await quick(A, '10.00')
    const b = await quick(A, '20.00')
    expect(a.clientOpId).toMatch(/^op-[A-Za-z0-9-]{8,}$/) // the server's accepted id shape
    expect(a.clientOpId).not.toBe(b.clientOpId)
    expect((await listQueue(A)).map((o) => o.total)).toEqual(['10.00', '20.00'])
    expect((await listQueue(A))[0].status).toBe('PENDING')
  })

  it('keeps each shop and person in a separate database, so one cannot read the other', async () => {
    await quick(A)
    expect(await listQueue(B)).toEqual([])
    expect(dbName(A)).not.toBe(dbName(B))
    await quick(B, '5.00')
    expect((await listQueue(A)).map((o) => o.total)).toEqual(['80.00'])
    expect((await listQueue(B)).map((o) => o.total)).toEqual(['5.00'])
    expect((await allDatabaseNames()).sort()).toEqual([dbName(A), dbName(B)].sort())
  })

  it('deleting one person\'s data leaves another\'s', async () => {
    await quick(A)
    await quick(B)
    await deleteScope(A)
    expect(await listQueue(A)).toEqual([])
    expect(await listQueue(B)).toHaveLength(1)
    await deleteAllScopes()
    expect(await listQueue(B)).toEqual([])
  })

  it('never stores a password, a token or a balance', async () => {
    await quick(A)
    const db = await openDb(A)
    const stores = [...db.objectStoreNames]
    db.close()
    expect(stores.sort()).toEqual(['queue', 'snapshots'])
    expect(JSON.stringify(await listQueue(A))).not.toMatch(/password|token|csrf|session/i)
  })
})

describe('the sync engine', () => {
  it('sends operations oldest first with their own ids and marks them synced', async () => {
    const a = await quick(A, '10.00')
    const b = await quick(A, '20.00')
    const mock = routes({ 'POST /api/v1/sync/operations': (init) => json(200, { results: bodyOf(init).operations.map((o) => result(o.client_op_id, 'SYNCED', { result: { total: '1.00' } })) }) })
    const out = await syncQueue(A)
    expect(out).toMatchObject({ sent: 2, synced: 2, stoppedBecause: 'done' })
    expect(bodyOf(mock.mock.calls[0][1] as RequestInit).operations.map((o) => o.client_op_id)).toEqual([a.clientOpId, b.clientOpId])
    expect((await listQueue(A)).map((o) => o.status)).toEqual(['SYNCED', 'SYNCED'])
  })

  it('a network failure leaves it waiting with the SAME id, never dropped, and a later try succeeds once', async () => {
    const op = await quick(A)
    routes({ 'POST /api/v1/sync/operations': () => Promise.reject(new TypeError('Failed to fetch')) })
    expect((await syncQueue(A)).stoppedBecause).toBe('offline')
    const [waiting] = await listQueue(A)
    expect(waiting).toMatchObject({ clientOpId: op.clientOpId, status: 'PENDING', attempts: 1 })
    const mock = routes({ 'POST /api/v1/sync/operations': (init) => json(200, { results: bodyOf(init).operations.map((o) => result(o.client_op_id, 'SYNCED')) }) })
    await syncQueue(A)
    expect(bodyOf(mock.mock.calls[0][1] as RequestInit).operations[0].client_op_id).toBe(op.clientOpId)
    expect((await listQueue(A))[0].status).toBe('SYNCED')
  })

  it('an answer that was lost (sent twice) is harmless: the server says duplicate and the client just records it as synced', async () => {
    await quick(A)
    routes({ 'POST /api/v1/sync/operations': (init) => json(200, { results: bodyOf(init).operations.map((o) => result(o.client_op_id, 'SYNCED', { duplicate: true })) }) })
    await syncQueue(A)
    expect((await listQueue(A))[0].status).toBe('SYNCED')
  })

  it('a session that ended pauses the sync and keeps everything', async () => {
    await quick(A)
    routes({ 'POST /api/v1/sync/operations': () => json(401, { message: 'no session' }) })
    expect((await syncQueue(A)).stoppedBecause).toBe('signed_out')
    expect((await listQueue(A))[0].status).toBe('PENDING')
  })

  it('a conflict stays on the device with the server\'s reason and is not retried by itself', async () => {
    const op = await quick(A)
    const mock = routes({ 'POST /api/v1/sync/operations': (init) => json(200, { results: bodyOf(init).operations.map((o) => result(o.client_op_id, 'CONFLICT', { error_code: 'insufficient_stock', message: 'Only 3 in stock.' })) }) })
    const out = await syncQueue(A)
    expect(out).toMatchObject({ synced: 0, needsAttention: 1 })
    expect(await listQueue(A)).toMatchObject([{ clientOpId: op.clientOpId, status: 'CONFLICT', errorCode: 'insufficient_stock', message: 'Only 3 in stock.' }])
    await syncQueue(A) // nothing pending: the conflict is not sent again
    expect(mock).toHaveBeenCalledTimes(1)
  })

  it('a server "retry" (an unexpected error) keeps it waiting without a tight loop', async () => {
    await quick(A)
    const mock = routes({ 'POST /api/v1/sync/operations': (init) => json(200, { results: bodyOf(init).operations.map((o) => result(o.client_op_id, 'RETRY', { message: 'try later' })) }) })
    await syncQueue(A)
    expect(mock).toHaveBeenCalledTimes(1)
    expect((await listQueue(A))[0]).toMatchObject({ status: 'PENDING', message: 'try later' })
  })

  it('an operation left "syncing" by a crash is sent again with the same id', async () => {
    const op = await quick(A)
    await updateOp(A, op.clientOpId, { status: 'SYNCING' })
    const mock = routes({ 'POST /api/v1/sync/operations': (init) => json(200, { results: bodyOf(init).operations.map((o) => result(o.client_op_id, 'SYNCED')) }) })
    await syncQueue(A)
    expect(bodyOf(mock.mock.calls[0][1] as RequestInit).operations[0].client_op_id).toBe(op.clientOpId)
  })

  it('only one sync runs at a time', async () => {
    await quick(A)
    let release: (r: Response) => void = () => undefined
    const mock = routes({ 'POST /api/v1/sync/operations': () => new Promise<Response>((r) => { release = r }) })
    const first = syncQueue(A)
    await waitFor(() => expect(mock).toHaveBeenCalled())
    expect((await syncQueue(A)).stoppedBecause).toBe('busy')
    const op = (await listQueue(A))[0]
    release(new Response(JSON.stringify({ results: [result(op.clientOpId, 'SYNCED')] }), { status: 200 }))
    await first
    expect(mock).toHaveBeenCalledTimes(1)
  })

  it('retry asks the server to run the same payload again, discard tells the server and never applies it', async () => {
    const op = await quick(A)
    routes({ 'POST /api/v1/sync/operations': (init) => json(200, { results: bodyOf(init).operations.map((o) => result(o.client_op_id, 'CONFLICT', { error_code: 'insufficient_stock' })) }) })
    await syncQueue(A)
    const conflict = (await listQueue(A))[0]
    const mock = routes({ [`POST /api/v1/sync/operations/${op.clientOpId}/retry`]: () => json(200, result(op.clientOpId, 'SYNCED', { result: { total: '80.00' } })) })
    await retryConflict(A, conflict)
    expect(mock).toHaveBeenCalledTimes(1)
    expect((await listQueue(A))[0]).toMatchObject({ status: 'SYNCED', result: { total: '80.00' } })

    const second = await quick(A, '5.00')
    await updateOp(A, second.clientOpId, { status: 'CONFLICT' })
    const discard = routes({ [`POST /api/v1/sync/operations/${second.clientOpId}/discard`]: () => json(200, result(second.clientOpId, 'DISCARDED')) })
    await discardOp(A, (await listQueue(A)).find((o) => o.clientOpId === second.clientOpId)!)
    expect(discard).toHaveBeenCalledTimes(1)
    expect((await listQueue(A)).find((o) => o.clientOpId === second.clientOpId)?.status).toBe('DISCARDED')
  })

  it('dropping something the server never saw needs no server call', async () => {
    const op = await quick(A)
    const mock = routes({})
    await discardOp(A, op)
    expect(mock).not.toHaveBeenCalled()
    expect((await listQueue(A))[0].status).toBe('DISCARDED')
  })
})

describe('snapshots (the read copy)', () => {
  const product = (over: Partial<SnapshotProduct> = {}): SnapshotProduct => ({ id: 1, name: 'Rice', sku: 'RICE-1', barcode: '8901234567890', brand: null, category: 'Grain', unit: 'pcs', allows_decimal: false, selling_price: '50.00', stock: '10.000', ...over })

  it('is stamped with the server\'s time and read back per person', async () => {
    routes({
      'GET /api/v1/sync/snapshot/products': () => json(200, { as_of: '2026-09-24T05:00:00Z', items: [product()], truncated: false }),
      'GET /api/v1/sync/snapshot/customers': () => json(200, { as_of: '2026-09-24T05:00:00Z', items: [{ id: 3, name: 'Asha', phone: '9876543210' }], truncated: false }),
    })
    expect(await refreshSnapshots(A)).toEqual({ products: true, customers: true })
    expect((await readSnapshot<SnapshotProduct>(A, 'products'))?.asOf).toBe('2026-09-24T05:00:00Z')
    expect(await readSnapshot(B, 'products')).toBeUndefined() // another person's device copy is not this one's
  })

  it('a role that may not see customers keeps nothing of them', async () => {
    routes({
      'GET /api/v1/sync/snapshot/products': () => json(200, { as_of: 'x', items: [], truncated: false }),
      'GET /api/v1/sync/snapshot/customers': () => json(403, { message: 'no' }),
    })
    expect(await refreshSnapshots(A)).toEqual({ products: true, customers: false })
    expect(await readSnapshot(A, 'customers')).toBeUndefined()
  })

  it('matches a barcode or SKU exactly and nothing fuzzy', () => {
    const items = [product(), product({ id: 2, name: 'Sugar', sku: 'SUG-1', barcode: null })]
    expect(findByCode(items, '8901234567890')?.name).toBe('Rice')
    expect(findByCode(items, 'sug-1')?.name).toBe('Sugar')
    expect(findByCode(items, '89012345')).toBeUndefined()
    expect(findByCode(items, '  ')).toBeUndefined()
  })
})

describe('signing out', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })
  })
  afterEach(() => {
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
  })

  it('removes everything when nothing is waiting', async () => {
    routes({})
    await refreshSnapshots(A).catch(() => undefined)
    await enqueue(A, { type: 'QUICK_SALE', label: 'x', payload: {} })
    const [op] = await listQueue(A)
    await updateOp(A, op.clientOpId, { status: 'SYNCED' })
    await signOutCleanup(A)
    expect(await allDatabaseNames()).toEqual([])
  })

  it('never destroys unsynced work without the person choosing to', async () => {
    await quick(A)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true) // "keep it"
    await signOutCleanup(A)
    expect(confirm).toHaveBeenCalled()
    expect(await listQueue(A)).toHaveLength(1)
    expect(await readSnapshot(A, 'products')).toBeUndefined() // but the read copies are always removed
    confirm.mockReturnValue(false) // "delete it": their explicit decision
    await signOutCleanup(A)
    expect(await listQueue(A)).toEqual([])
    confirm.mockRestore()
  })

  it('does nothing without a shop', async () => {
    await expect(signOutCleanup(null)).resolves.toBeUndefined()
  })
})

function auth(scope: Scope | null): AuthValue {
  return {
    status: 'ready', session: scope ? ({ shop_id: scope.shopId, active_user_id: scope.userId } as never) : null, expired: false, endsInMinutes: null, can: () => true,
    refresh: async () => undefined, signedIn: () => undefined, chooseShop: async () => undefined, signOut: async () => undefined, stayActive: async () => undefined,
  }
}
function page(scope: Scope | null = A) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}><AuthContext.Provider value={auth(scope)}><MemoryRouter><OfflinePage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>,
  )
}

describe('the offline till page', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true })
  })
  afterEach(() => {
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true })
  })

  it('says it is offline and warns about what device storage means', async () => {
    page()
    expect(await screen.findByText(/You are offline/)).toBeInTheDocument()
    expect(screen.getByText(/Anyone who can open this device and browser could read them/)).toBeInTheDocument()
  })

  it('queues a quick sale with the total the person was quoted, and it survives a reload of the page', async () => {
    const { unmount } = page()
    fireEvent.change(await screen.findByLabelText('Amount'), { target: { value: '120.50' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save on this device' }))
    expect(await screen.findByText(/Saved. It will be sent/)).toBeInTheDocument()
    const [op] = await listQueue(A)
    expect(op).toMatchObject({ type: 'QUICK_SALE', status: 'PENDING', total: '120.50' })
    expect(op.payload).toMatchObject({ create: { gross_amount: '120.50' }, payment: { payment_method: 'CASH' }, expected_total: '120.50' })
    unmount()
    page()
    expect(await screen.findByTestId('op-PENDING')).toHaveTextContent('Quick sale')
  })

  it('refuses a bad amount and credit without a customer, and saves nothing', async () => {
    page()
    fireEvent.change(await screen.findByLabelText('Amount'), { target: { value: '12.345' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save on this device' }))
    expect(await screen.findByText('Enter an amount like 250.50.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText(/Amount paid now/), { target: { value: '40' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save on this device' }))
    expect(await screen.findByText('A sale that is not paid in full needs a customer.')).toBeInTheDocument()
    expect(await listQueue(A)).toEqual([])
  })

  it('builds a sale from the device copy, prices it exactly, and sends no price so the server\'s price wins', async () => {
    routes({
      'GET /api/v1/sync/snapshot/products': () => json(200, { as_of: '2026-09-24T05:00:00Z', items: [{ id: 7, name: 'Rice', sku: 'R1', barcode: '111', brand: null, category: 'G', unit: 'kg', allows_decimal: true, selling_price: '48.33', stock: '10.000' }], truncated: false }),
      'GET /api/v1/sync/snapshot/customers': () => json(200, { as_of: '2026-09-24T05:00:00Z', items: [], truncated: false }),
    })
    await refreshSnapshots(A)
    page()
    fireEvent.change(await screen.findByLabelText('What happened'), { target: { value: 'SALE' } })
    const box = await screen.findByLabelText('Scan or type a barcode, SKU or name')
    fireEvent.change(box, { target: { value: '111' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    fireEvent.change(await screen.findByLabelText('Quantity'), { target: { value: '2.5' } })
    expect(await screen.findByText(/Total: ₹120.83/)).toBeInTheDocument() // 2.5 x 48.33 = 120.825 -> rounded half up, as the server does
    expect(screen.getByText(/stock was 10.000 kg/)).toBeInTheDocument() // stale stock is labelled as what it was
    fireEvent.click(screen.getByRole('button', { name: 'Save on this device' }))
    await waitFor(async () => expect(await listQueue(A)).toHaveLength(1))
    const [op] = await listQueue(A)
    expect(op.payload).toMatchObject({ create: { items: [{ product_id: 7, quantity: '2.5' }] }, expected_total: '120.83' })
    expect(JSON.stringify(op.payload)).not.toContain('unit_price')
  })

  it('shows a conflict with the reason, both totals, and retry and drop buttons', async () => {
    const op = await enqueue(A, { type: 'SALE', label: 'Sale: 1 item(s)', total: '100.00', payload: {} })
    await updateOp(A, op.clientOpId, { status: 'CONFLICT', errorCode: 'total_changed', message: 'x', result: { expected_total: '100.00', server_total: '120.00' } })
    page()
    const row = await screen.findByTestId('op-CONFLICT')
    expect(within(row).getByText('The price changed since this was quoted.')).toBeInTheDocument()
    expect(within(row).getByText(/You quoted ₹100.00 but the shop now prices it at ₹120.00/)).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Try again' })).toBeDisabled() // no connection: cannot retry now
    expect(within(row).getByRole('button', { name: 'Drop it' })).toBeInTheDocument()
  })

  it('labels the device copy with when it was made and warns when it is very old', async () => {
    routes({
      'GET /api/v1/sync/snapshot/products': () => json(200, { as_of: '2020-01-01T00:00:00Z', items: [], truncated: false }),
      'GET /api/v1/sync/snapshot/customers': () => json(200, { as_of: '2020-01-01T00:00:00Z', items: [], truncated: false }),
    })
    await refreshSnapshots(A)
    localStorage.setItem(`shop-offline-copy:${A.shopId}:${A.userId}`, '1')
    page()
    await waitFor(() => expect(screen.getByTestId('last-synced')).toHaveTextContent(/Last synced:/))
    expect(await screen.findByText(/several days old/)).toBeInTheDocument()
    localStorage.clear()
  })

  it('shows nothing of another shop', async () => {
    await quick(B, '999.00')
    page(A)
    expect(await screen.findByText('Nothing is waiting.')).toBeInTheDocument()
    expect(screen.queryByText(/999/)).toBeNull()
  })
})

describe('the service worker and manifest (static release files)', () => {
  const sw = swText
  const manifest = JSON.parse(manifestText) as Record<string, unknown>
  const iconFiles = Object.keys(import.meta.glob('../public/icons/*.png', { query: '?url' }))

  it('never caches business data, only the app shell', () => {
    for (const path of ['/api/', '/health', '/metrics', '/docs']) expect(sw).toContain(`'${path}'`)
    expect(sw).toMatch(/if \(request\.method !== 'GET'\) return/)
    expect(sw).toMatch(/isNever\(url\)\) return/)
    expect(sw).not.toMatch(/cache\.put\([^)]*api/i)
    expect(sw).not.toMatch(/localStorage|password|token|authorization|cookie/i)
  })

  it('waits for the person to accept an update, cleans old caches and can be cleared', () => {
    expect(sw).toContain('SKIP_WAITING')
    expect(sw).toContain('CLEAR_CACHES')
    expect(sw).toMatch(/key\.startsWith\('shop-'\) && key !== CACHE/)
    expect(sw).not.toMatch(/install[\s\S]{0,200}self\.skipWaiting\(\)/) // an update never swaps itself in
  })

  it('has what a browser needs to offer installation', () => {
    expect(manifest).toMatchObject({ display: 'standalone', start_url: expect.any(String), scope: '/', name: expect.any(String), short_name: expect.any(String) })
    const icons = manifest.icons as { sizes: string; type: string; purpose: string }[]
    expect(icons.some((i) => i.sizes === '192x192' && i.type === 'image/png')).toBe(true)
    expect(icons.some((i) => i.sizes === '512x512' && i.purpose === 'maskable')).toBe(true)
    const html = indexHtml
    expect(html).toContain('rel="manifest"')
    expect(html).toContain('viewport-fit=cover')
    for (const icon of manifest.icons as { src: string }[]) expect(iconFiles).toContain(`../public${icon.src}`) // every icon the manifest names exists
  })
})

describe('translations', () => {
  it('has Hindi for every offline and pwa string with the same placeholders', () => {
    const walk = (a: unknown, b: unknown, path: string) => {
      if (typeof a === 'string') {
        expect(typeof b, path).toBe('string')
        expect((a.match(/{{\w+}}/g) ?? []).sort(), path).toEqual(((b as string).match(/{{\w+}}/g) ?? []).sort())
        return
      }
      for (const key of Object.keys(a as object)) walk((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key], `${path}.${key}`)
    }
    walk(en.offline, hi.offline, 'offline')
    walk(en.pwa, hi.pwa, 'pwa')
  })
})
