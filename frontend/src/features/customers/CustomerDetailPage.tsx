import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, HandCoins, Pencil, Power, Scale, Undo2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { getCustomer, getLedger, setCustomerActive } from '@/api/customers'
import type { LedgerEntry, LedgerEntryType } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { Alert, Badge, Button, LinkButton, QueryError, Spinner } from '@/components/ui'
import { formatDate, formatMoney, NOT_SET } from '@/lib/format'

import { BalanceAmount, BalanceBadge } from './BalanceDisplay'
import { statusOf } from './balance'
import { AdjustmentForm, OpeningBalanceForm, PaymentForm, ReverseForm, type FormKind } from './KhataForms'

const PAGE_SIZE = 20
const ENTRY_TYPES: LedgerEntryType[] = ['OPENING_BALANCE', 'CREDIT_SALE', 'PAYMENT', 'RETURN_CREDIT', 'ADJUSTMENT', 'REVERSAL']
/** Entries a person may undo by hand. Entries from a sale or a return are undone by cancelling that document. */
const REVERSIBLE: LedgerEntryType[] = ['PAYMENT', 'OPENING_BALANCE', 'ADJUSTMENT']

export function CustomerDetailPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const location = useLocation()
  const customerId = Number(useParams().id)
  const validId = Number.isInteger(customerId) && customerId > 0
  const warnings = (location.state as { warnings?: string[] } | null)?.warnings ?? []
  const [form, setForm] = useState<FormKind | null>(null)

  const customer = useQuery({ queryKey: ['customer', customerId], queryFn: () => getCustomer(customerId), enabled: validId })
  // Is there a live (not reversed) opening balance? If so, adding another is not offered.
  const openings = useQuery({
    queryKey: ['customerLedger', customerId, 'opening'],
    queryFn: () => getLedger(customerId, { entry_type: 'OPENING_BALANCE', limit: 50 }),
    enabled: validId,
  })

  const toggleActive = useMutation({
    mutationFn: (active: boolean) => setCustomerActive(customerId, active),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['customer', customerId] }),
        queryClient.invalidateQueries({ queryKey: ['customers'] }),
      ])
    },
  })

  if (!validId || customer.isError) {
    const notFound = !validId || (customer.error instanceof ApiError && customer.error.status === 404)
    return (
      <div className="space-y-6">
        {notFound ? <Alert tone="error">{t('customers.detail.notFound')}</Alert> : <QueryError error={customer.error} onRetry={() => void customer.refetch()} />}
        <LinkButton to="/customers" variant="secondary">
          {t('customers.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (!customer.data) return <Spinner />

  const c = customer.data
  const hasLiveOpening = openings.data?.items.some((entry) => entry.reversed_by_entry_id === null) ?? true // hidden until known

  function confirmAndToggle() {
    if (c.is_active && !window.confirm(t('customers.detail.deactivateConfirm'))) return
    toggleActive.mutate(!c.is_active)
  }
  const close = () => setForm(null)

  return (
    <div className="space-y-6">
      <Link to="/customers" className="inline-flex min-h-10 items-center gap-2 text-emerald-800 hover:underline">
        <ArrowLeft aria-hidden="true" className="size-5" />
        {t('customers.detail.backToList')}
      </Link>

      {warnings.length > 0 && (
        <Alert tone="warning">
          <p className="font-medium">{t('customers.detail.savedWithWarnings')}</p>
          <ul className="list-disc pl-5">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Alert>
      )}
      {!c.is_active && <Alert tone="warning">{t('customers.detail.inactiveNotice')}</Alert>}
      {toggleActive.isError && <Alert tone="error">{t('common.genericError')}</Alert>}

      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold text-slate-900">{c.name}</h1>
          <Badge tone={c.is_active ? 'green' : 'slate'}>{c.is_active ? t('common.active') : t('common.inactive')}</Badge>
        </div>
        <div className="flex flex-wrap gap-3">
          <LinkButton to={`/customers/${c.id}/crm`} variant="secondary" requires="CRM_VIEW">
            {t('crm.customer.openCrm')}
          </LinkButton>
          <LinkButton to={`/customers/${c.id}/edit`} variant="secondary">
            <Pencil aria-hidden="true" className="size-5" />
            {t('common.edit')}
          </LinkButton>
          <Button variant={c.is_active ? 'danger' : 'primary'} loading={toggleActive.isPending} onClick={confirmAndToggle}>
            <Power aria-hidden="true" className="size-5" />
            {c.is_active ? t('customers.detail.deactivate') : t('customers.detail.activate')}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('customers.detail.balanceHeading')}</h2>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <BalanceAmount balance={c.balance} status={c.balance_status} large />
            <BalanceBadge status={c.balance_status} />
          </div>
          <p className="mt-3 text-sm text-slate-600">{t(`customers.detail.explain.${c.balance_status}`)}</p>
          <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-100 pt-4">
            <div>
              <dt className="text-sm text-slate-500">{t('customers.detail.owes')}</dt>
              <dd className="font-semibold text-slate-900">{formatMoney(c.outstanding)}</dd>
            </div>
            <div>
              <dt className="text-sm text-slate-500">{t('customers.detail.advance')}</dt>
              <dd className="font-semibold text-slate-900">{formatMoney(c.advance)}</dd>
            </div>
          </dl>
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('customers.detail.contact')}</h2>
          <dl className="mt-3 space-y-3">
            <Row label={t('customers.form.fields.phone')}>{c.phone ?? NOT_SET}</Row>
            <Row label={t('customers.form.fields.email')}>{c.email ?? NOT_SET}</Row>
            <Row label={t('customers.form.fields.address')}>{c.address ?? NOT_SET}</Row>
            <Row label={t('customers.form.fields.notes')}>{c.notes ?? NOT_SET}</Row>
          </dl>
        </section>
      </div>

      <section className="space-y-4">
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => setForm('payment')}>
            <HandCoins aria-hidden="true" className="size-5" />
            {t('customers.khata.addPayment')}
          </Button>
          {!hasLiveOpening && (
            <Button variant="secondary" onClick={() => setForm('opening')}>
              {t('customers.khata.addOpening')}
            </Button>
          )}
          {c.is_active && (
            <LinkButton to={`/sales/new?customer=${c.id}`} variant="secondary">
              {t('customers.khata.newSale')}
            </LinkButton>
          )}
          <Button variant="secondary" onClick={() => setForm('adjustment')}>
            <Scale aria-hidden="true" className="size-5" />
            {t('customers.khata.addAdjustment')}
          </Button>
        </div>
        {form === 'payment' && <PaymentForm customerId={c.id} outstanding={c.outstanding} onDone={close} />}
        {form === 'opening' && <OpeningBalanceForm customerId={c.id} onDone={close} />}
        {form === 'adjustment' && <AdjustmentForm customerId={c.id} onDone={close} />}
      </section>

      <LedgerSection customerId={c.id} />
    </div>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="whitespace-pre-line font-medium text-slate-900">{children}</dd>
    </div>
  )
}

function LedgerSection({ customerId }: { customerId: number }) {
  const { t } = useTranslation()
  const [type, setType] = useState<LedgerEntryType | ''>('')
  const [offset, setOffset] = useState(0)
  const [reversing, setReversing] = useState<LedgerEntry | null>(null)

  const ledger = useQuery({
    queryKey: ['customerLedger', customerId, { type }, offset],
    queryFn: () => getLedger(customerId, { entry_type: type, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })

  return (
    <section id="ledger" className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-lg font-semibold text-slate-900">{t('customers.ledger.heading')}</h2>
        <FilterSelect
          aria-label={t('customers.ledger.filterType')}
          value={type}
          onChange={(event) => {
            setType(event.target.value as LedgerEntryType | '')
            setOffset(0)
          }}
        >
          <option value="">{t('customers.ledger.allTypes')}</option>
          {ENTRY_TYPES.map((entryType) => (
            <option key={entryType} value={entryType}>
              {t(`customers.entryTypes.${entryType}`)}
            </option>
          ))}
        </FilterSelect>
      </div>

      {reversing && <ReverseForm customerId={customerId} entry={reversing} onDone={() => setReversing(null)} />}

      {ledger.isError && <QueryError error={ledger.error} onRetry={() => void ledger.refetch()} />}
      {ledger.isPending && <Spinner />}
      {ledger.data?.total === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white px-4 py-8 text-center text-slate-600">
          {type ? t('customers.ledger.noMatch') : t('customers.ledger.empty')}
        </p>
      )}

      {ledger.data && ledger.data.total > 0 && (
        <>
          <LedgerTable rows={ledger.data.items} onReverse={setReversing} />
          <LedgerCards rows={ledger.data.items} onReverse={setReversing} />
          <Pagination total={ledger.data.total} limit={ledger.data.limit} offset={ledger.data.offset} onChange={setOffset} />
          <div className="border-t border-slate-200 pt-4">
            <p className="mb-2 text-sm font-medium text-slate-700">{t('customers.ledger.exportHeading')}</p>
            <ExportButtons kind={`customers/${customerId}/ledger`} filters={{ entry_type: type }} />
          </div>
        </>
      )}
    </section>
  )
}

const canReverse = (entry: LedgerEntry) => REVERSIBLE.includes(entry.entry_type) && entry.reversed_by_entry_id === null

/** What an entry refers to, in words: a sale, a return, the entry it undoes or that undid it. */
function Details({ entry }: { entry: LedgerEntry }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-0.5 text-sm">
      {entry.payment_method && (
        <div className="font-medium">
          {t(`customers.khata.methods.${entry.payment_method}`)}
          {entry.payment_reference ? ` · ${entry.payment_reference}` : ''}
        </div>
      )}
      {entry.reference_type === 'SALE' && entry.reference_id ? (
        <Link to={`/sales/${entry.reference_id}`} className="text-emerald-800 hover:underline">
          {t('customers.ledger.reference.SALE')} {entry.reference_no ?? `#${entry.reference_id}`}
        </Link>
      ) : (
        entry.reference_type && (
          <div className="text-slate-600">
            {t(`customers.ledger.reference.${entry.reference_type as 'SALE' | 'QUICK_SALE' | 'SALES_RETURN'}`)} #{entry.reference_id}
          </div>
        )
      )}
      {entry.reverses_entry_id && <div className="text-slate-600">{t('customers.ledger.reverses', { id: entry.reverses_entry_id })}</div>}
      {entry.note && <div className="text-slate-700">{entry.note}</div>}
      {entry.reversed_by_entry_id && <Badge tone="slate">{t('customers.ledger.reversedBadge')}</Badge>}
    </div>
  )
}

function LedgerTable({ rows, onReverse }: { rows: LedgerEntry[]; onReverse: (entry: LedgerEntry) => void }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'
  return (
    <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('customers.ledger.columns.date')}</th>
            <th className={heading}>{t('customers.ledger.columns.type')}</th>
            <th className={heading}>{t('customers.ledger.columns.details')}</th>
            <th className={`${heading} text-right`}>{t('customers.ledger.columns.debit')}</th>
            <th className={`${heading} text-right`}>{t('customers.ledger.columns.credit')}</th>
            <th className={`${heading} text-right`}>{t('customers.ledger.columns.balance')}</th>
            <th className={heading}>
              <span className="sr-only">{t('customers.ledger.columns.actions')}</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((entry) => {
            const owed = !entry.amount_delta.startsWith('-')
            const amount = owed ? entry.amount_delta : entry.amount_delta.slice(1)
            return (
              <tr key={entry.id} className={entry.reversed_by_entry_id ? 'text-slate-500' : ''}>
                <td className="whitespace-nowrap px-4 py-3 text-sm">{formatDate(entry.entry_date)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium">{t(`customers.entryTypes.${entry.entry_type}`)}</td>
                <td className="min-w-48 px-4 py-3">
                  <Details entry={entry} />
                  <div className="text-xs text-slate-500">{entry.created_by_name}</div>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right font-semibold text-red-700">{owed ? formatMoney(amount) : ''}</td>
                <td className="whitespace-nowrap px-4 py-3 text-right font-semibold text-emerald-700">{owed ? '' : formatMoney(amount)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                  <BalanceAmount balance={entry.balance_after} status={statusOf(entry.balance_after)} />
                </td>
                <td className="px-4 py-3 text-right">
                  {canReverse(entry) && (
                    <Button variant="secondary" onClick={() => onReverse(entry)}>
                      <Undo2 aria-hidden="true" className="size-4" />
                      {t('customers.ledger.reverse')}
                    </Button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** The same history as cards, for phones. */
function LedgerCards({ rows, onReverse }: { rows: LedgerEntry[]; onReverse: (entry: LedgerEntry) => void }) {
  const { t } = useTranslation()
  return (
    <ul className="space-y-3 md:hidden">
      {rows.map((entry) => {
        const owed = !entry.amount_delta.startsWith('-')
        const amount = owed ? entry.amount_delta : entry.amount_delta.slice(1)
        return (
          <li key={entry.id} className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${entry.reversed_by_entry_id ? 'opacity-70' : ''}`}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-semibold text-slate-900">{t(`customers.entryTypes.${entry.entry_type}`)}</p>
                <p className="text-sm text-slate-500">{formatDate(entry.entry_date)}</p>
              </div>
              <p className={`text-lg font-bold ${owed ? 'text-red-700' : 'text-emerald-700'}`}>
                {owed ? '+' : '−'}
                {formatMoney(amount)}
              </p>
            </div>
            <div className="mt-2">
              <Details entry={entry} />
            </div>
            <div className="mt-3 flex items-center justify-between text-sm text-slate-600">
              <span>{t('customers.ledger.columns.balance')}</span>
              <BalanceAmount balance={entry.balance_after} status={statusOf(entry.balance_after)} />
            </div>
            {canReverse(entry) && (
              <div className="mt-3">
                <Button variant="secondary" onClick={() => onReverse(entry)}>
                  <Undo2 aria-hidden="true" className="size-4" />
                  {t('customers.ledger.reverse')}
                </Button>
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
