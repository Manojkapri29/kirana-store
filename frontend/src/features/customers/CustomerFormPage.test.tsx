import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CustomerFormPage } from './CustomerFormPage'

function answer(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

const saved = {
  customer: { id: 7, name: 'Asha Stores', phone: null, email: null, address: null, notes: null, is_active: true, balance: '0.00' },
  warnings: [],
}

function renderForm() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/customers/new']}>
        <Routes>
          <Route path="/customers/new" element={<CustomerFormPage mode="create" />} />
          <Route path="/customers/:id" element={<p>Customer page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('a save that fails (draft preservation and safe retry)', () => {
  it('keeps what was typed, explains calmly, and a retry sends the same idempotency key', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() =>
        answer(503, { success: false, error_code: 'database_busy', message: 'The system is busy.', category: 'database', retryable: true, reference_id: 'ERR-20260921-B7K2M' }),
      )
      .mockImplementationOnce(() => answer(201, saved))
    vi.stubGlobal('fetch', fetchMock)
    renderForm()

    fireEvent.change(screen.getByLabelText(/Customer name/), { target: { value: 'Asha Stores' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add customer' }))

    // The failure is shown plainly, with a reference and the promise that nothing was lost.
    expect(await screen.findByText('Reference: ERR-20260921-B7K2M')).toBeInTheDocument()
    expect(screen.getByText('Your data has been preserved on this screen.')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('database_busy')
    expect(screen.getByLabelText(/Customer name/)).toHaveValue('Asha Stores')

    // Try again: the same request, the same key, so the server can never make two customers.
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.getByText('Customer page')).toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const keyOf = (call: number) => new Headers((fetchMock.mock.calls[call][1] as RequestInit).headers).get('Idempotency-Key')
    expect(keyOf(0)).toBeTruthy()
    expect(keyOf(1)).toBe(keyOf(0))
  })

  it('does not show a success page when the server never confirmed', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    renderForm()
    fireEvent.change(screen.getByLabelText(/Customer name/), { target: { value: 'Asha Stores' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add customer' }))
    expect(await screen.findByText('The connection was interrupted.')).toBeInTheDocument()
    expect(screen.queryByText('Customer page')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/Customer name/)).toHaveValue('Asha Stores')
  })

  it('offers a restore of an unsaved form found from earlier, and applies it only when chosen', async () => {
    const draft = { name: 'Earlier Name', phone: '', email: '', address: '', notes: '', openingBalance: '' }
    localStorage.setItem('shop.draft.customer.new', JSON.stringify({ savedAt: Date.now(), value: draft }))
    vi.stubGlobal('fetch', vi.fn())
    renderForm()
    expect(screen.getByText('We found changes from earlier that were not saved.')).toBeInTheDocument()
    expect(screen.getByLabelText(/Customer name/)).toHaveValue('') // not applied silently
    fireEvent.click(screen.getByRole('button', { name: 'Restore them' }))
    expect(screen.getByLabelText(/Customer name/)).toHaveValue('Earlier Name')
  })
})
