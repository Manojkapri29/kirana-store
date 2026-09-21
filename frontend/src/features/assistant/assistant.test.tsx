import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AiAction, AiStatus, Answer } from '@/api/ai'

import { ActionPreviewCard } from './ActionPreviewCard'
import { AnswerView } from './AnswerView'
import { AssistantPage } from './AssistantPage'

function respond(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

const answer = (over: Partial<Answer> = {}): Answer => ({
  status: 'ANSWERED', tool: 'get_sales_summary', title: 'Sales', message: 'Net sales for today were ₹318.00 from 3 sales.',
  figures: [{ label: 'Net sales', value: '₹318.00', note: null }], table: null, sources: ['Based on sales data from 21 Sep 2026'],
  period: null, notes: [], badges: [], proposals: [], export: null, follow_ups: [], provider_used: false, ...over,
})

const status = (over: Partial<AiStatus> = {}): AiStatus => ({
  configured: false, provider_label: null, documents: false,
  features: { ai_assistant: true, ai_insights: true, ai_documents: true },
  usage: { period: '2026-09', requests: 3, limit: 30, failed: 0, input_tokens: 0, output_tokens: 0, by_feature: {} },
  suggestions: [{ label: "Today's Sales", question: 'How much did I sell today?' }], ...over,
})

const action = (over: Partial<AiAction> = {}): AiAction => ({
  id: 5, kind: 'PURCHASE_DRAFT', status: 'PROPOSED', feature: 'purchase_suggestions', created_at: '2026-09-21T10:00:00Z', decided_at: null,
  attempts: 0, current: { supplier_id: 1, items: [{ product_id: 2, quantity: '25', unit_cost: '20' }] },
  preview: {
    shop: 'Shop A', title: 'Create Purchase Draft', summary: 'Supplier: Sharma Traders',
    lines: { columns: ['Product', 'Quantity', 'Unit price', 'Line total'], rows: [['Product RICE', '25 pcs', '₹20.00', '₹500.00']] },
    totals: [{ label: 'Estimated total', value: '₹500.00' }],
    impact: ['This will create a purchase DRAFT for Sharma Traders with 1 line.', 'Nothing is posted and no stock changes.'],
    problems: [], warnings: [], can_confirm: true,
  },
  result_type: null, result_ids: null, failure_message: null, reference_id: null, ...over,
})

function renderWith(ui: React.ReactNode, route = '/assistant') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('AnswerView', () => {
  it('shows the message, the real figures and where they came from', () => {
    renderWith(<AnswerView answer={answer()} />)
    expect(screen.getByText('Net sales for today were ₹318.00 from 3 sales.')).toBeInTheDocument()
    expect(screen.getByText('₹318.00', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('Based on sales data from 21 Sep 2026')).toBeInTheDocument()
  })

  it('labels a recommendation as AI Recommendation and says it is for review', () => {
    renderWith(<AnswerView answer={answer({ badges: ['AI Recommendation'] })} />)
    expect(screen.getByText('AI Recommendation')).toBeInTheDocument()
    expect(screen.getByText(/Review it before acting/)).toBeInTheDocument()
  })

  it('marks answers that are not data (no data, not available, refused)', () => {
    renderWith(<AnswerView answer={answer({ status: 'NOT_AVAILABLE', message: 'Online ordering is not part of this application yet.' })} />)
    expect(screen.getByText('Not available')).toBeInTheDocument()
  })

  it('a proposal is only a button to prepare a draft, and preparing changes nothing by itself', () => {
    const onPropose = vi.fn()
    const proposal = { kind: 'PURCHASE_DRAFT' as const, feature: 'purchase_suggestions', label: 'Create purchase draft for Sharma Traders', payload: {} }
    renderWith(<AnswerView answer={answer({ proposals: [proposal] })} onPropose={onPropose} />)
    expect(screen.getByText(/Nothing is created until you confirm/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: proposal.label }))
    expect(onPropose).toHaveBeenCalledWith(proposal)
  })

  it('offers follow-up questions', () => {
    const onFollowUp = vi.fn()
    renderWith(<AnswerView answer={answer({ follow_ups: ['Which products are low in stock?'] })} onFollowUp={onFollowUp} />)
    fireEvent.click(screen.getByRole('button', { name: 'Which products are low in stock?' }))
    expect(onFollowUp).toHaveBeenCalledWith('Which products are low in stock?')
  })
})

describe('ActionPreviewCard (Confirm, Edit, Cancel)', () => {
  it('shows exactly what will happen before anything happens', () => {
    renderWith(<ActionPreviewCard action={action()} onChange={() => undefined} />)
    expect(screen.getByText('Review before anything happens')).toBeInTheDocument()
    expect(screen.getByText('Create Purchase Draft')).toBeInTheDocument()
    expect(screen.getByText(/Shop: Shop A/)).toBeInTheDocument()
    expect(screen.getByText('Product RICE')).toBeInTheDocument()
    expect(screen.getByText('₹500.00', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('Nothing is posted and no stock changes.')).toBeInTheDocument()
    for (const name of ['Confirm', 'Edit', 'Cancel']) expect(screen.getByRole('button', { name })).toBeInTheDocument()
  })

  it('cannot be confirmed while there are problems, and lists them', () => {
    const blocked = action({ preview: { ...action().preview, problems: ['Enter the price for 1 line before confirming.'], can_confirm: false } })
    renderWith(<ActionPreviewCard action={blocked} onChange={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled()
    expect(screen.getByText('Enter the price for 1 line before confirming.')).toBeInTheDocument()
  })

  it('says it is done only after the server confirmed, and never says posted', async () => {
    const done = action({ status: 'EXECUTED', result_type: 'purchase', result_ids: [9] })
    const fetchMock = vi.fn(() => respond(200, done))
    vi.stubGlobal('fetch', fetchMock)
    const onChange = vi.fn()
    renderWith(<ActionPreviewCard action={action()} onChange={onChange} />)
    expect(screen.queryByText(/Purchase draft created/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(done))
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[0]).toContain('/actions/5/confirm')
  })

  it('shows a calm failure with the reference and keeps the action open', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(500, { success: false, error_code: 'internal_error', message: 'Something went wrong while completing this action.', category: 'unexpected', retryable: false, reference_id: 'ERR-20260921-Q7K2M', detail: 'x' })))
    renderWith(<ActionPreviewCard action={action()} onChange={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(await screen.findByText('Reference: ERR-20260921-Q7K2M')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument()
  })

  it('cancelling asks the server to cancel, and editing sends the changed values', async () => {
    const fetchMock = vi.fn(() => respond(200, action({ status: 'CANCELLED' })))
    vi.stubGlobal('fetch', fetchMock)
    renderWith(<ActionPreviewCard action={action()} onChange={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[0]).toContain('/actions/5/cancel')
  })

  it('edit changes the quantity and price, then applies them through the server', async () => {
    const fetchMock = vi.fn(() => respond(200, action()))
    vi.stubGlobal('fetch', fetchMock)
    renderWith(<ActionPreviewCard action={action()} onChange={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '40' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply changes' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('/actions/5')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(init.body as string).payload.items[0].quantity).toBe('40')
  })

  it('a finished action offers a link to the record and no more buttons to change it', () => {
    renderWith(<ActionPreviewCard action={action({ status: 'EXECUTED', result_type: 'purchase', result_ids: [9] })} onChange={() => undefined} />)
    expect(screen.getByText(/It is not posted/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the draft' })).toHaveAttribute('href', '/purchases/9')
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
  })
})

describe('AssistantPage', () => {
  function routes(map: Record<string, () => Promise<Response>>) {
    return vi.fn((url: string) => {
      const key = Object.keys(map).find((k) => String(url).includes(k))
      return key ? map[key]() : respond(404, { detail: 'nope' })
    })
  }

  it('says "AI Assistant is not configured." while suggested questions still work', async () => {
    vi.stubGlobal('fetch', routes({ '/ai/status': () => respond(200, status()), '/ai/ask': () => respond(200, answer()) }))
    renderWith(<AssistantPage />)
    expect(await screen.findByText('AI Assistant is not configured.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: "Today's Sales" }))
    expect(await screen.findByText('Net sales for today were ₹318.00 from 3 sales.')).toBeInTheDocument()
    expect(screen.getByText('AI requests this month: 3 of 30')).toBeInTheDocument()
  })

  it('shows a plan message when the plan has no assistant', async () => {
    vi.stubGlobal('fetch', routes({ '/ai/status': () => respond(200, status({ features: { ai_assistant: false, ai_insights: false, ai_documents: false }, usage: null })) }))
    renderWith(<AssistantPage />)
    expect(await screen.findByText('The AI assistant is not part of your plan.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask' })).not.toBeInTheDocument()
  })

  it('shows "AI Assistant is temporarily unavailable." with Try again, and the retry works', async () => {
    let calls = 0
    vi.stubGlobal('fetch', routes({
      '/ai/status': () => respond(200, status({ configured: true })),
      '/ai/ask': () => (++calls === 1 ? respond(503, { success: false, error_code: 'ai_unavailable', message: 'AI Assistant is temporarily unavailable.', category: 'external_api', retryable: true, reference_id: 'ERR-20260921-AAAAA', detail: 'x' }) : respond(200, answer())),
    }))
    const { container } = renderWith(<AssistantPage />)
    await screen.findByText(/AI requests this month/)
    fireEvent.change(screen.getByLabelText('Ask your Business Assistant'), { target: { value: 'kitna maal nikla aaj bhai' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('AI Assistant is temporarily unavailable.')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/Traceback|anthropic|api[_-]?key|sqlalchemy/i)
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Net sales for today were ₹318.00 from 3 sales.')).toBeInTheDocument()
  })

  it('never renders provider secrets or internals from a status response', async () => {
    vi.stubGlobal('fetch', routes({ '/ai/status': () => respond(200, status({ configured: true, provider_label: 'Anthropic' })) }))
    const { container } = renderWith(<AssistantPage />)
    await screen.findByText(/AI requests this month/)
    expect(container.textContent).not.toMatch(/sk-|x-api-key|api[_-]?key/i)
  })
})
