import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { checkPrices } from '@/api/priceIntelligence'
import type { PriceQuote, PriceResult, Product } from '@/api/types'
import { TextField } from '@/components/fields'
import { Alert, Badge, Button } from '@/components/ui'
import { useEntitlements } from '@/features/subscription/useEntitlements'
import { formatDate, formatDateTime, formatMoney } from '@/lib/format'

function QuoteRow({ quote }: { quote: PriceQuote }) {
  const { t } = useTranslation()
  const sameCurrency = quote.currency_matches_shop
  const diff = quote.difference
  const direction = diff === null ? null : diff.startsWith('-') ? t('priceCheck.below') : diff === '0.00' ? t('priceCheck.same') : t('priceCheck.above')
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-xl font-bold text-slate-900">
          {sameCurrency ? formatMoney(quote.price) : `${quote.currency} ${quote.price}`}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={quote.match_type === 'EXACT' ? 'green' : 'amber'}>{quote.match_type === 'EXACT' ? t('priceCheck.exact') : t('priceCheck.possible')}</Badge>
          <span className="text-xs text-slate-600">{t('priceCheck.confidence', { percent: quote.confidence })}</span>
        </div>
      </div>
      {quote.product_name && <p className="mt-1 text-slate-800">{quote.product_name}</p>}
      <p className="mt-1 text-sm text-slate-600">
        {quote.source_label}
        {quote.location ? ` · ${quote.location}` : ''}
        {quote.location_matched && <span className="ml-2 font-medium text-emerald-800">{t('priceCheck.locationMatch')}</span>}
      </p>
      <p className="text-xs text-slate-500">
        {quote.observed_on && `${t('priceCheck.seen', { date: formatDate(quote.observed_on) })} · `}
        {quote.checked_at && t('priceCheck.checkedAt', { when: formatDateTime(quote.checked_at) })}
      </p>
      {quote.stale && <p className="mt-1 text-sm font-medium text-amber-800">{t('priceCheck.stale')}</p>}
      {!sameCurrency && <p className="mt-1 text-sm text-slate-600">{t('priceCheck.differentCurrency')}</p>}
      {diff !== null && (
        <p className="mt-1 text-sm text-slate-700">
          {t('priceCheck.difference', { amount: formatMoney(diff.replace('-', '')) })} ({direction})
        </p>
      )}
      {quote.source_url && (
        <a href={quote.source_url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-block text-sm text-emerald-800 hover:underline">
          {t('priceCheck.openSource')}
        </a>
      )}
    </li>
  )
}

/**
 * Outside prices for one product: information only. It never changes the product's MRP, selling price, purchase
 * price or cost, and nothing here can stop a bill. A source that is missing, down or slow is shown as a status.
 */
export function PriceCheckPanel({ product }: { product: Product }) {
  const { t } = useTranslation()
  const { allows } = useEntitlements()
  const [barcode, setBarcode] = useState(product.barcode ?? '')
  const [city, setCity] = useState('')
  const [state, setState] = useState('')
  const [market, setMarket] = useState('')
  const [problem, setProblem] = useState<string | null>(null)

  const run = useMutation({
    mutationFn: () =>
      checkPrices({
        product_id: product.id,
        barcode: barcode.trim() === '' ? null : barcode.trim(),
        city: city.trim() || null,
        state: state.trim() || null,
        market: market.trim() || null,
      }),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 403) {
        setProblem(error.message.includes('reached') ? t('priceCheck.limitReached') : t('priceCheck.planNeeded'))
      } else if (error instanceof ApiError && error.status === 422) setProblem(error.message)
      else setProblem(t('priceCheck.failed'))
    },
  })

  if (allows('price_intelligence') === false) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{t('priceCheck.heading')}</h2>
        <p className="mt-2 text-slate-600">{t('priceCheck.planNeeded')}</p>
      </section>
    )
  }

  const result: PriceResult | undefined = run.data
  function submit(event: FormEvent) {
    event.preventDefault()
    setProblem(null)
    run.mutate()
  }

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">{t('priceCheck.heading')}</h2>
        <p className="mt-1 text-sm text-slate-600">{t('priceCheck.intro')}</p>
      </div>
      <form onSubmit={submit} noValidate className="space-y-3">
        {product.barcode === null && <p className="text-sm text-slate-600">{t('priceCheck.noBarcode')}</p>}
        <TextField label={t('priceCheck.barcode')} hint={t('priceCheck.barcodeHint')} value={barcode} inputMode="numeric" onChange={(e) => setBarcode(e.target.value)} maxLength={50} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <TextField label={t('priceCheck.city')} value={city} onChange={(e) => setCity(e.target.value)} maxLength={80} optional />
          <TextField label={t('priceCheck.state')} value={state} onChange={(e) => setState(e.target.value)} maxLength={80} optional />
          <TextField label={t('priceCheck.market')} value={market} onChange={(e) => setMarket(e.target.value)} maxLength={80} optional />
        </div>
        <p className="text-xs text-slate-500">{t('priceCheck.locationHint')}</p>
        <Button type="submit" loading={run.isPending}>{run.isPending ? t('priceCheck.checking') : t('priceCheck.check')}</Button>
      </form>

      {problem && <Alert tone="warning">{problem}</Alert>}

      {result && (
        <div className="space-y-3">
          <p className="text-sm text-slate-700">{t('priceCheck.yourPrice', { price: formatMoney(product.selling_price) })}</p>
          {result.identified_as?.name && <p className="text-sm text-slate-600">{t('priceCheck.identified', { name: result.identified_as.name })}</p>}
          <Alert tone={result.location.applied ? 'info' : 'warning'}>{result.location.note}</Alert>
          {result.quotes.length === 0 && result.notes.map((note) => <Alert key={note} tone="info">{note}</Alert>)}
          {result.quotes.length > 0 && (
            <ul className="space-y-3">
              {result.quotes.map((q, i) => (
                <QuoteRow key={`${q.source}-${q.price}-${q.location}-${i}`} quote={q} />
              ))}
            </ul>
          )}
          {result.quotes.length > 0 && result.notes.map((note) => <p key={note} className="text-sm text-slate-600">{note}</p>)}
          <div>
            <p className="mb-1 text-sm font-semibold text-slate-700">{t('priceCheck.providers')}</p>
            <ul className="space-y-1 text-sm text-slate-600">
              {result.providers.map((p) => (
                <li key={p.name}>
                  <span className="font-medium text-slate-800">{p.label}</span>: {t(`priceCheck.states.${p.state}`)}
                  <span className="text-slate-500"> — {p.message}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  )
}
