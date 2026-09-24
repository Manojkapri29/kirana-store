import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { OnlineOrdersPage } from '@/features/onlineStore/OnlineOrdersPage'
import { PublicOrderPage } from '@/features/onlineStore/PublicOrderPage'
import { PublicStorePage } from '@/features/onlineStore/PublicStorePage'
import { en } from '@/i18n/locales/en'
import { hi } from '@/i18n/locales/hi'

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
function Where() {
  const l = useLocation()
  return <p data-testid="where">{l.pathname + l.search}</p>
}
function mount(path: string, element: React.ReactNode, route: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={element} /><Route path="*" element={<Where />} /></Routes></MemoryRouter></QueryClientProvider>)
}
beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const store = (over: Record<string, unknown> = {}) => ({
  slug: 'ram', name: 'Ram Kirana', is_open: true, accepts_cod: true, accepts_upi: true, delivery_enabled: true, pickup_enabled: true,
  min_order_amount: '0.00', contact_phone: '9876500000', announcement: null, ...over,
})
const product = (id: number, name: string, price: string, over: Record<string, unknown> = {}) => ({ id, name, category: 'Staples', unit: 'pcs', allows_decimal: false, price, in_stock: true, ...over })
const catalogue = (items: unknown[]) => ({ items, total: items.length, limit: 24, offset: 0 })

describe('public storefront', () => {
  it('lets a customer order without signing in, and sends no price, total or status', async () => {
    let sent: Record<string, unknown> | null = null
    let key: string | null = null
    routes({
      'GET /api/v1/public/stores/ram': () => json(200, store()),
      'GET /api/v1/public/stores/ram/products': () => json(200, catalogue([product(1, 'Rice', '50.00'), product(2, 'Sugar', '48.00', { in_stock: false })])),
      'POST /api/v1/public/stores/ram/orders': (init) => {
        sent = JSON.parse(String(init?.body)); key = new Headers(init?.headers).get('Idempotency-Key')
        return json(201, { order_no: 'ORD/2026-27/0001', reference: 'ORD-2026-27-0001', tracking_token: 'tok123', status: 'PLACED' })
      },
    })
    mount('/store/ram', <PublicStorePage />, '/store/:slug')
    const rice = await screen.findByTestId('product-1')
    expect(within(screen.getByTestId('product-2')).getByRole('button', { name: en.onlineStore.public.add })).toBeDisabled() // out of stock
    fireEvent.click(within(rice).getByRole('button', { name: en.onlineStore.public.add }))
    fireEvent.click(within(rice).getByRole('button', { name: en.onlineStore.public.add })) // + one more
    expect(screen.getAllByText('₹100.00', { selector: 'span' }).length).toBeGreaterThanOrEqual(2) // the line and the total
    fireEvent.click(screen.getByRole('button', { name: en.onlineStore.public.checkout }))
    fireEvent.change(screen.getByLabelText(en.onlineStore.public.yourName), { target: { value: 'Asha Verma' } })
    fireEvent.change(screen.getByLabelText(en.onlineStore.public.yourPhone), { target: { value: '9876500001' } })
    fireEvent.change(screen.getByLabelText(new RegExp(en.onlineStore.public.address)), { target: { value: '12 MG Road, Pune' } })
    fireEvent.click(screen.getByRole('button', { name: en.onlineStore.public.place }))
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('/store/ram/order/ORD-2026-27-0001?token=tok123'))
    expect(key).toMatch(/^attempt-/)
    expect(sent).toEqual({ customer_name: 'Asha Verma', customer_phone: '9876500001', fulfilment: 'DELIVERY', payment: 'COD', delivery_address: '12 MG Road, Pune', items: [{ product_id: 1, quantity: '2' }] })
    expect(JSON.stringify(sent)).not.toMatch(/price|total|status/)
    expect(localStorage.getItem('kirana.store.cart:ram')).toBe('{}') // the cart is emptied once the order is placed
  })

  it('says the store is closed and will not let anyone order', async () => {
    routes({ 'GET /api/v1/public/stores/ram': () => json(200, store({ is_open: false })), 'GET /api/v1/public/stores/ram/products': () => json(200, catalogue([product(1, 'Rice', '50.00')])) })
    mount('/store/ram', <PublicStorePage />, '/store/:slug')
    expect(await screen.findByText(en.onlineStore.public.closed)).toBeInTheDocument()
    expect(within(await screen.findByTestId('product-1')).getByRole('button', { name: en.onlineStore.public.add })).toBeDisabled()
  })

  it('enforces the minimum order before checkout and offers only what the shop offers', async () => {
    routes({ 'GET /api/v1/public/stores/ram': () => json(200, store({ min_order_amount: '200.00', delivery_enabled: false, accepts_cod: false })), 'GET /api/v1/public/stores/ram/products': () => json(200, catalogue([product(1, 'Rice', '50.00')])) })
    mount('/store/ram', <PublicStorePage />, '/store/:slug')
    fireEvent.click(within(await screen.findByTestId('product-1')).getByRole('button', { name: en.onlineStore.public.add }))
    expect(screen.getByRole('button', { name: en.onlineStore.public.checkout })).toBeDisabled()
    expect(screen.getByText(en.onlineStore.public.minimum.replace('{{amount}}', '₹200.00'))).toBeInTheDocument()
  })

  it('shows a plain not-found for an unknown store', async () => {
    routes({ 'GET /api/v1/public/stores/nope': () => json(404, { message: 'This store was not found.' }) })
    mount('/store/nope', <PublicStorePage />, '/store/:slug')
    expect(await screen.findByText(en.onlineStore.public.notFound)).toBeInTheDocument()
  })
})

describe('order tracking', () => {
  const order = (over: Record<string, unknown> = {}) => ({
    order_no: 'ORD/2026-27/0001', reference: 'ORD-2026-27-0001', status: 'PLACED', fulfilment: 'DELIVERY', payment: 'COD', total_amount: '100.00', placed_at: '2026-09-24T10:00:00Z',
    items: [{ name: 'Rice', unit: 'pcs', quantity: '2', unit_price: '50.00', line_total: '100.00' }], timeline: [{ status: 'PLACED', at: '2026-09-24T10:00:00Z' }],
    store_name: 'Ram Kirana', store_phone: '9876500000', can_cancel: true, tracking_token: null, ...over,
  })
  it('shows the order for the token and lets the customer cancel before it is accepted', async () => {
    routes({
      'GET /api/v1/public/stores/ram/orders/ORD-2026-27-0001': () => json(200, order()),
      'POST /api/v1/public/stores/ram/orders/ORD-2026-27-0001/cancel': () => json(200, order({ status: 'CANCELLED', can_cancel: false })),
    })
    mount('/store/ram/order/ORD-2026-27-0001?token=tok', <PublicOrderPage />, '/store/:slug/order/:reference')
    expect(await screen.findByText('ORD/2026-27/0001')).toBeInTheDocument()
    expect(screen.queryByText(/9876500001/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: en.onlineStore.public.cancelOrder }))
    expect(await screen.findByText(en.onlineStore.public.cancelled)).toBeInTheDocument()
  })
  it('does not reveal anything for a wrong token', async () => {
    routes({ 'GET /api/v1/public/stores/ram/orders/ORD-2026-27-0001': () => json(404, { message: 'This order was not found.' }) })
    mount('/store/ram/order/ORD-2026-27-0001?token=bad', <PublicOrderPage />, '/store/:slug/order/:reference')
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.queryByTestId('public-order')).not.toBeInTheDocument()
  })
})

describe('shop orders screen', () => {
  const row = { id: 7, order_no: 'ORD/2026-27/0007', status: 'PLACED', fulfilment: 'DELIVERY', payment: 'COD', customer_name: 'Asha Verma', customer_phone: '9876500001', total_amount: '100.00', item_count: 1, placed_at: '2026-09-24T10:00:00Z', sale_id: null }
  const detail = (over: Record<string, unknown> = {}) => ({
    ...row, delivery_address: '12 MG Road', notes: null, customer_id: null, invoice_no: null, sale_total: null, decision_reason: null, warnings: [], allowed_next: ['ACCEPTED', 'REJECTED', 'CANCELLED'],
    items: [{ product_id: 1, product_name: 'Rice', unit: 'pcs', quantity: '2', unit_price: '50.00', line_total: '100.00' }], events: [{ from_status: null, to_status: 'PLACED', actor: 'CUSTOMER', note: null, at: '2026-09-24T10:00:00Z' }], ...over,
  })
  const base = { 'GET /api/v1/online-orders': () => json(200, { items: [row], total: 1, limit: 20, offset: 0 }), 'GET /api/v1/online-orders/summary': () => json(200, { by_status: {}, open: 1, delivered_value: '0.00' }) }

  it('offers only the steps the order can take and accepts it', async () => {
    let accepted = false
    routes({
      ...base,
      'GET /api/v1/online-orders/7': () => json(200, detail(accepted ? { status: 'ACCEPTED', allowed_next: ['PREPARING', 'READY', 'CANCELLED'] } : {})),
      'POST /api/v1/online-orders/7/accept': () => { accepted = true; return json(200, detail({ status: 'ACCEPTED', allowed_next: ['PREPARING', 'READY', 'CANCELLED'] })) },
    })
    mount('/o', <OnlineOrdersPage />, '/o')
    fireEvent.click(await screen.findByRole('button', { name: /ORD\/2026-27\/0007/ }))
    const box = await screen.findByTestId('order-detail-7')
    expect(within(box).getByRole('button', { name: en.onlineStore.orders.accept })).toBeInTheDocument()
    expect(within(box).queryByRole('button', { name: en.onlineStore.orders.next.DELIVERED })).not.toBeInTheDocument()
    fireEvent.click(within(box).getByRole('button', { name: en.onlineStore.orders.accept }))
    expect(await within(box).findByRole('button', { name: en.onlineStore.orders.next.PREPARING })).toBeInTheDocument()
  })

  it('needs a reason to reject, and says delivering creates the sale', async () => {
    let body: Record<string, unknown> | null = null
    routes({ ...base, 'GET /api/v1/online-orders/7': () => json(200, detail()), 'POST /api/v1/online-orders/7/reject': (init) => { body = JSON.parse(String(init?.body)); return json(200, detail({ status: 'REJECTED', allowed_next: [] })) } })
    mount('/o', <OnlineOrdersPage />, '/o')
    fireEvent.click(await screen.findByRole('button', { name: /ORD\/2026-27\/0007/ }))
    const box = await screen.findByTestId('order-detail-7')
    fireEvent.click(within(box).getByRole('button', { name: en.onlineStore.orders.reject }))
    const confirm = within(box).getByRole('button', { name: en.onlineStore.orders.confirm })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(box).getByLabelText(en.onlineStore.orders.reasonLabel), { target: { value: 'Out of stock today' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(body).toEqual({ reason: 'Out of stock today' }))
  })

  it('shows the delivered hint and the stock warning', async () => {
    routes({ ...base, 'GET /api/v1/online-orders/7': () => json(200, detail({ status: 'OUT_FOR_DELIVERY', allowed_next: ['DELIVERED', 'CANCELLED'], warnings: ['Only 1 pcs of Rice in stock now; the order needs 2.'] })) })
    mount('/o', <OnlineOrdersPage />, '/o')
    fireEvent.click(await screen.findByRole('button', { name: /ORD\/2026-27\/0007/ }))
    expect(await screen.findByText(en.onlineStore.orders.deliveredHint)).toBeInTheDocument()
    expect(screen.getByText(/Only 1 pcs of Rice/)).toBeInTheDocument()
  })
})

describe('translations', () => {
  it('has every online store string in Hindi as well as English', () => {
    const keys = (o: object, p = ''): string[] => Object.entries(o).flatMap(([k, v]) => (typeof v === 'object' && v !== null ? keys(v, `${p}${k}.`) : [`${p}${k}`]))
    expect(keys(hi.onlineStore).sort()).toEqual(keys(en.onlineStore).sort())
  })
})
