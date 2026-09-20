import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { listProducts } from '@/api/products'
import { SearchInput } from '@/components/SearchInput'
import { useDebounced } from '@/hooks/useDebounced'
import { formatMoney, formatQuantity } from '@/lib/format'

import { pickedFromProduct, type PickedProduct } from './purchaseForm'

interface ProductPickerProps {
  label: string
  value: PickedProduct | null
  onChange: (product: PickedProduct | null) => void
  error?: string
  autoFocus?: boolean
}

/**
 * Search active products by name, SKU, brand or barcode and pick one. A barcode scanner types the code and
 * presses Enter: if exactly one product matches, it is chosen at once.
 */
export function ProductPicker({ label, value, onChange, error, autoFocus }: ProductPickerProps) {
  const { t } = useTranslation()
  const labelId = useId()
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim(), 250)

  const results = useQuery({
    queryKey: ['products', { picker: q }],
    queryFn: () => listProducts({ q, status: 'active', limit: 8 }),
    enabled: q !== '',
  })

  async function pickOnEnter() {
    const text = search.trim()
    if (!text) return
    try {
      const found = await listProducts({ q: text, status: 'active', limit: 2 })
      if (found.total === 1) {
        onChange(pickedFromProduct(found.items[0]))
        setSearch('')
      }
    } catch {
      // The result list under the box shows load problems; a failed scan just leaves it as it is.
    }
  }

  if (value) {
    return (
      <div>
        <p id={labelId} className="mb-1.5 text-sm font-medium text-slate-800">
          {label}
        </p>
        <div
          aria-labelledby={labelId}
          className={`flex min-h-12 items-center justify-between gap-2 rounded-lg border bg-slate-50 px-3 py-1 ${error ? 'border-red-500' : 'border-slate-300'}`}
        >
          <span className="min-w-0">
            <span className="block truncate font-medium text-slate-900">{value.name}</span>
            <span className="font-mono text-xs text-slate-500">{value.sku}</span>
          </span>
          <button
            type="button"
            onClick={() => onChange(null)}
            aria-label={t('purchases.form.changeProduct')}
            className="flex size-10 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-200"
          >
            <X aria-hidden="true" className="size-5" />
          </button>
        </div>
        {error && (
          <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">
            {error}
          </p>
        )}
      </div>
    )
  }

  return (
    <div>
      <p id={labelId} className="mb-1.5 text-sm font-medium text-slate-800">
        {label}
      </p>
      <SearchInput
        value={search}
        onChange={setSearch}
        onEnter={() => void pickOnEnter()}
        placeholder={t('purchases.form.searchProduct')}
        autoFocus={autoFocus}
      />
      {error && (
        <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">
          {error}
        </p>
      )}
      {q !== '' && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-white shadow-sm">
          {results.isPending && <p className="px-3 py-3 text-sm text-slate-500">{t('common.loading')}</p>}
          {results.isError && <p className="px-3 py-3 text-sm text-red-700">{t('common.loadError')}</p>}
          {results.data?.items.length === 0 && (
            <p className="px-3 py-3 text-sm text-slate-600">{t('purchases.form.noProducts')}</p>
          )}
          {results.data && results.data.items.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {results.data.items.map((product) => (
                <li key={product.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onChange(pickedFromProduct(product))
                      setSearch('')
                    }}
                    className="flex min-h-12 w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-emerald-50"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-slate-900">{product.name}</span>
                      <span className="font-mono text-xs text-slate-500">{product.sku}</span>
                    </span>
                    <span className="shrink-0 text-right text-xs text-slate-600">
                      <span className="block">
                        {formatQuantity(product.current_stock)} {product.unit_code}
                      </span>
                      <span className="block">{formatMoney(product.avg_cost)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
