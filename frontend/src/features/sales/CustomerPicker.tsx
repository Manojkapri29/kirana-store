import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { listCustomers } from '@/api/customers'
import { SearchInput } from '@/components/SearchInput'
import { useDebounced } from '@/hooks/useDebounced'
import { BalanceAmount } from '@/features/customers/BalanceDisplay'

export interface PickedCustomer {
  id: number
  name: string
  phone: string | null
}

interface CustomerPickerProps {
  label: string
  value: PickedCustomer | null
  onChange: (customer: PickedCustomer | null) => void
  error?: string
}

/** Search active customers by name or phone. Optional for a cash sale; required for a sale on credit. */
export function CustomerPicker({ label, value, onChange, error }: CustomerPickerProps) {
  const { t } = useTranslation()
  const labelId = useId()
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim(), 250)
  const results = useQuery({
    queryKey: ['customers', { picker: q }],
    queryFn: () => listCustomers({ q, status: 'active', limit: 6 }),
    enabled: q !== '',
  })

  return (
    <div>
      <p id={labelId} className="mb-1.5 text-sm font-medium text-slate-800">
        {label} <span className="font-normal text-slate-500">({t('common.optional')})</span>
      </p>
      {value ? (
        <div
          aria-labelledby={labelId}
          className={`flex min-h-12 items-center justify-between gap-2 rounded-lg border bg-slate-50 px-3 py-1 ${error ? 'border-red-500' : 'border-slate-300'}`}
        >
          <span className="min-w-0">
            <span className="block truncate font-medium text-slate-900">{value.name}</span>
            {value.phone && <span className="text-xs text-slate-500">{value.phone}</span>}
          </span>
          <button
            type="button"
            onClick={() => onChange(null)}
            aria-label={t('sales.form.removeCustomer')}
            className="flex size-10 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-200"
          >
            <X aria-hidden="true" className="size-5" />
          </button>
        </div>
      ) : (
        <>
          <SearchInput value={search} onChange={setSearch} placeholder={t('sales.form.searchCustomer')} />
          {q !== '' && (
            <div className="mt-2 rounded-lg border border-slate-200 bg-white shadow-sm">
              {results.isPending && <p className="px-3 py-3 text-sm text-slate-500">{t('common.loading')}</p>}
              {results.data?.items.length === 0 && <p className="px-3 py-3 text-sm text-slate-600">{t('sales.form.noCustomers')}</p>}
              {results.data && results.data.items.length > 0 && (
                <ul className="divide-y divide-slate-100">
                  {results.data.items.map((customer) => (
                    <li key={customer.id}>
                      <button
                        type="button"
                        onClick={() => {
                          onChange({ id: customer.id, name: customer.name, phone: customer.phone })
                          setSearch('')
                        }}
                        className="flex min-h-12 w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-emerald-50"
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-slate-900">{customer.name}</span>
                          {customer.phone && <span className="text-xs text-slate-500">{customer.phone}</span>}
                        </span>
                        <span className="shrink-0 text-xs">
                          <BalanceAmount balance={customer.balance} status={customer.balance_status} />
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}
      {error && (
        <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}
