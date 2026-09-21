import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { listCustomers } from '@/api/customers'
import type { BalanceFilter, Customer, StatusFilter } from '@/api/types'
import { ExportButtons } from '@/components/ExportButtons'
import { FilterSelect } from '@/components/fields'
import { Pagination } from '@/components/Pagination'
import { SearchInput } from '@/components/SearchInput'
import { Badge, EmptyState, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useDebounced } from '@/hooks/useDebounced'
import { NOT_SET } from '@/lib/format'

import { BalanceAmount, BalanceBadge } from './BalanceDisplay'

const PAGE_SIZE = 25

export function CustomersPage() {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<StatusFilter>('active')
  const [balance, setBalance] = useState<BalanceFilter>('any')
  const [biggestFirst, setBiggestFirst] = useState(false)
  const [offset, setOffset] = useState(0)
  const q = useDebounced(search.trim())

  const filters = { q, status, balance, sort: biggestFirst ? ('balance' as const) : ('name' as const) }
  const customers = useQuery({
    queryKey: ['customers', filters, offset],
    queryFn: () => listCustomers({ ...filters, limit: PAGE_SIZE, offset }),
    placeholderData: keepPreviousData,
  })

  const data = customers.data
  const isFiltering = q !== '' || status !== 'active' || balance !== 'any'
  const addButton = (
    <LinkButton to="/customers/new">
      <Plus aria-hidden="true" className="size-5" />
      {t('customers.add')}
    </LinkButton>
  )
  const reset = <T,>(set: (value: T) => void) => (value: T) => {
    set(value)
    setOffset(0)
  }

  return (
    <div className="space-y-6">
      <PageHeader title={t('customers.title')} subtitle={t('customers.subtitle')} actions={addButton} />

      <div className="flex flex-col gap-3 xl:flex-row">
        <SearchInput value={search} onChange={reset(setSearch)} placeholder={t('customers.searchPlaceholder')} autoFocus />
        <FilterSelect
          aria-label={t('customers.filters.balance')}
          value={balance}
          onChange={(event) => reset(setBalance)(event.target.value as BalanceFilter)}
        >
          <option value="any">{t('customers.filters.anyBalance')}</option>
          <option value="outstanding">{t('customers.filters.owing')}</option>
          <option value="advance">{t('customers.filters.advance')}</option>
          <option value="settled">{t('customers.filters.settled')}</option>
        </FilterSelect>
        <FilterSelect
          aria-label={t('customers.columns.status')}
          value={status}
          onChange={(event) => reset(setStatus)(event.target.value as StatusFilter)}
        >
          <option value="active">{t('common.active')}</option>
          <option value="inactive">{t('common.inactive')}</option>
          <option value="all">{t('common.all')}</option>
        </FilterSelect>
      </div>
      <label className="flex min-h-10 w-fit items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={biggestFirst}
          onChange={(event) => reset(setBiggestFirst)(event.target.checked)}
          className="size-5 accent-emerald-600"
        />
        {t('customers.filters.biggestFirst')}
      </label>

      {customers.isError && <QueryError onRetry={() => void customers.refetch()} />}
      {customers.isPending && <Spinner />}

      {data && data.items.length === 0 && (
        <EmptyState
          title={isFiltering ? t('customers.noMatch') : t('customers.empty')}
          hint={isFiltering ? undefined : t('customers.emptyHint')}
          action={!isFiltering && addButton}
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <CustomerTable items={data.items} />
          <CustomerCards items={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
          <div className="border-t border-slate-200 pt-4">
            <p className="mb-2 text-sm font-medium text-slate-700">{t('customers.exportList')}</p>
            <ExportButtons kind="customers" filters={{ q, status, balance }} />
          </div>
        </>
      )}
    </div>
  )
}

function StatusBadge({ active }: { active: boolean }) {
  const { t } = useTranslation()
  return <Badge tone={active ? 'green' : 'slate'}>{active ? t('common.active') : t('common.inactive')}</Badge>
}

function CustomerTable({ items }: { items: Customer[] }) {
  const { t } = useTranslation()
  const heading = 'whitespace-nowrap px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500'

  return (
    <div className="hidden overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm md:block">
      <table className="min-w-full divide-y divide-slate-200">
        <thead className="bg-slate-50">
          <tr>
            <th className={heading}>{t('customers.columns.name')}</th>
            <th className={heading}>{t('customers.columns.phone')}</th>
            <th className={`${heading} text-right`}>{t('customers.columns.balance')}</th>
            <th className={heading}>{t('customers.columns.balanceStatus')}</th>
            <th className={heading}>{t('customers.columns.status')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {items.map((customer) => (
            <tr key={customer.id} className={`hover:bg-slate-50 ${customer.is_active ? '' : 'text-slate-500'}`}>
              <td className="min-w-48 px-4 py-3">
                <Link to={`/customers/${customer.id}`} className="font-medium text-emerald-800 hover:underline">
                  {customer.name}
                </Link>
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-sm">{customer.phone ?? NOT_SET}</td>
              <td className="whitespace-nowrap px-4 py-3 text-right">
                <BalanceAmount balance={customer.balance} status={customer.balance_status} />
              </td>
              <td className="px-4 py-3">
                <BalanceBadge status={customer.balance_status} />
              </td>
              <td className="px-4 py-3">
                <StatusBadge active={customer.is_active} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The same customers as tappable cards, for phones. */
function CustomerCards({ items }: { items: Customer[] }) {
  return (
    <ul className="space-y-3 md:hidden">
      {items.map((customer) => (
        <li key={customer.id}>
          <Link
            to={`/customers/${customer.id}`}
            className={`block rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${customer.is_active ? '' : 'opacity-70'}`}
          >
            <div className="flex items-start justify-between gap-3">
              <p className="font-semibold text-slate-900">{customer.name}</p>
              <StatusBadge active={customer.is_active} />
            </div>
            {customer.phone && <p className="mt-1 text-sm text-slate-600">{customer.phone}</p>}
            <div className="mt-3 flex items-center justify-between">
              <BalanceAmount balance={customer.balance} status={customer.balance_status} />
              <BalanceBadge status={customer.balance_status} />
            </div>
          </Link>
        </li>
      ))}
    </ul>
  )
}
