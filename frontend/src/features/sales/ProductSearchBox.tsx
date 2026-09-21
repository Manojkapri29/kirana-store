import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { listProducts } from '@/api/products'
import type { Product } from '@/api/types'
import { SearchInput } from '@/components/SearchInput'
import { useDebounced } from '@/hooks/useDebounced'
import { formatMoney, formatQuantity } from '@/lib/format'

interface ProductSearchBoxProps {
  onPick: (product: Product) => void
  autoFocus?: boolean
}

/**
 * Find an active product by name, SKU, brand or barcode and add it to the bill. A USB or Bluetooth barcode
 * scanner just types the code and presses Enter: if exactly one product matches, it is added at once and the
 * box is cleared, ready for the next scan. No scanner-specific code is involved.
 */
export function ProductSearchBox({ onPick, autoFocus }: ProductSearchBoxProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [scanMiss, setScanMiss] = useState(false)
  const q = useDebounced(search.trim(), 250)

  const results = useQuery({
    queryKey: ['products', { picker: q }],
    queryFn: () => listProducts({ q, status: 'active', limit: 8 }),
    enabled: q !== '',
  })

  function pick(product: Product) {
    onPick(product)
    setSearch('')
    setScanMiss(false)
  }

  async function pickOnEnter() {
    const text = search.trim()
    if (!text) return
    try {
      const found = await listProducts({ q: text, status: 'active', limit: 2 })
      if (found.total === 1) pick(found.items[0])
      else setScanMiss(found.total === 0)
    } catch {
      // The list under the box shows load problems; a failed scan just leaves the box as it is.
    }
  }

  return (
    <div>
      <SearchInput
        value={search}
        onChange={(value) => {
          setSearch(value)
          setScanMiss(false)
        }}
        onEnter={() => void pickOnEnter()}
        placeholder={t('sales.form.searchProduct')}
        autoFocus={autoFocus}
      />
      {scanMiss && (
        <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">
          {t('sales.form.noProductForCode')}
        </p>
      )}
      {q !== '' && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-white shadow-sm">
          {results.isPending && <p className="px-3 py-3 text-sm text-slate-500">{t('common.loading')}</p>}
          {results.isError && <p className="px-3 py-3 text-sm text-red-700">{t('common.loadError')}</p>}
          {results.data?.items.length === 0 && <p className="px-3 py-3 text-sm text-slate-600">{t('sales.form.noProducts')}</p>}
          {results.data && results.data.items.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {results.data.items.map((product) => (
                <li key={product.id}>
                  <button
                    type="button"
                    onClick={() => pick(product)}
                    className="flex min-h-12 w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-emerald-50"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-slate-900">{product.name}</span>
                      <span className="font-mono text-xs text-slate-500">{product.sku}</span>
                    </span>
                    <span className="shrink-0 text-right text-xs text-slate-600">
                      <span className="block font-semibold text-slate-900">{formatMoney(product.selling_price)}</span>
                      <span className="block">
                        {formatQuantity(product.current_stock)} {product.unit_code}
                      </span>
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
