import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '@/api/client'
import { ErrorNotice } from '@/components/ErrorNotice'
import { listProducts, lookupProduct } from '@/api/products'
import type { LookupProduct, LookupResult, Product } from '@/api/types'
import { CameraScanButton } from '@/components/CameraScanner'
import { SearchInput } from '@/components/SearchInput'
import { useDebounced } from '@/hooks/useDebounced'
import { isZero } from '@/lib/decimal'
import { formatMoney, formatQuantity } from '@/lib/format'

import { ScannedProductCard } from './ScannedProductCard'

interface ProductSearchBoxProps {
  onPick: (product: Product) => void
  autoFocus?: boolean
  /**
   * Use the smart scanner lookup on Enter (exact barcode, exact SKU, exact name, then search). `false` falls back to
   * the plain search, for a plan without barcode lookup. `undefined` (plan not known yet) behaves like `true`.
   */
  scanner?: boolean
}

interface Scanned {
  product: LookupProduct
  match: LookupResult['match_type']
}

/**
 * Find a product by name, SKU, brand or barcode and add it to the bill. A USB or Bluetooth barcode scanner just
 * types the code and presses Enter, so a scan and a typed entry are the same thing. On Enter the server looks the
 * code up: an exact barcode, SKU or name adds the product straight away (if it is active and in stock) and shows
 * its details; several matches are listed to pick from; an unknown code says "Barcode not found" and adds
 * nothing. Nothing here ever creates a product.
 */
export function ProductSearchBox({ onPick, autoFocus, scanner }: ProductSearchBoxProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [lookupFailure, setLookupFailure] = useState<{ error: unknown; text: string } | null>(null)
  const [choices, setChoices] = useState<Scanned[] | null>(null)
  const [scanned, setScanned] = useState<Scanned | null>(null)
  const q = useDebounced(search.trim(), 250)
  const useScanner = scanner !== false

  const results = useQuery({
    queryKey: ['products', { picker: q }],
    queryFn: () => listProducts({ q, status: 'active', limit: 8 }),
    enabled: q !== '',
  })

  function pick(product: Product) {
    onPick(product)
    setSearch('')
    setProblem(null)
    setChoices(null)
  }

  /** The scan path adds only a product that can be sold: active, and with some stock. */
  function admit(entry: Scanned) {
    const { product } = entry
    if (!product.is_active) {
      setProblem(t('scanner.inactive', { name: product.name }))
      setScanned(entry)
      return
    }
    if (isZero(product.current_stock) || product.current_stock.startsWith('-')) {
      setProblem(t('scanner.outOfStock', { name: product.name }))
      setScanned(entry)
      return
    }
    setScanned(entry)
    pick(product)
  }

  async function scan(text: string) {
    try {
      const result = await lookupProduct(text)
      if (!result.found) {
        setProblem(result.message === 'Barcode not found' ? t('scanner.notFound') : t('scanner.noMatch'))
        setScanned(null)
        return
      }
      const entries = result.products.map((product) => ({ product, match: result.match_type }))
      if (entries.length === 1 && result.match_type !== 'SEARCH') admit(entries[0])
      else if (entries.length === 1) setChoices(entries) // a best guess is shown for the person to confirm
      else setChoices(entries)
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) setProblem(t('scanner.planNeeded'))
      else setLookupFailure({ error, text })
    }
  }

  async function plainEnter(text: string) {
    try {
      const found = await listProducts({ q: text, status: 'active', limit: 2 })
      if (found.total === 1) pick(found.items[0])
      else if (found.total === 0) setProblem(t('sales.form.noProductForCode'))
    } catch {
      // The list under the box shows load problems; a failed scan just leaves the box as it is.
    }
  }

  async function onEnter() {
    const text = search.trim()
    if (!text) return
    setProblem(null)
    setChoices(null)
    setLookupFailure(null)
    if (useScanner) await scan(text)
    else await plainEnter(text)
  }

  return (
    <div>
      <div className="flex items-stretch gap-2">
        <SearchInput
          value={search}
          onChange={(value) => {
            setSearch(value)
            setProblem(null)
            setChoices(null)
          }}
          onEnter={() => void onEnter()}
          placeholder={useScanner ? t('scanner.placeholder') : t('sales.form.searchProduct')}
          autoFocus={autoFocus}
        />
        {useScanner && (
          <CameraScanButton
            onCode={(code) => {
              setSearch(code) // the camera hands the code to the very same lookup as a typed or USB-scanned one
              setProblem(null)
              setChoices(null)
              void scan(code)
            }}
          />
        )}
      </div>
      {useScanner && <p className="mt-1 text-xs text-slate-500">{t('scanner.hint')}</p>}
      {lookupFailure && (
        <div className="mt-2">
          <ErrorNotice
            error={lookupFailure.error}
            context="barcode"
            safeToRepeat
            retry={() => {
              const text = lookupFailure.text
              setLookupFailure(null)
              void scan(text)
            }}
            manual_entry={() => {
              // Billing never waits on the lookup service: fall back to searching your own products by the text.
              const text = lookupFailure.text
              setLookupFailure(null)
              void plainEnter(text)
            }}
          />
        </div>
      )}
      {problem && (
        <p role="alert" className="mt-1.5 text-sm font-medium text-red-700">
          {problem}
        </p>
      )}
      {problem && problem === t('scanner.notFound') && <p className="text-sm text-slate-600">{t('scanner.notFoundHint')}</p>}

      {choices && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-white shadow-sm">
          <p className="px-3 pt-3 text-sm font-medium text-slate-700">{t('scanner.pickOne')}</p>
          <ul className="divide-y divide-slate-100">
            {choices.map((entry) => (
              <li key={entry.product.id}>
                <button type="button" onClick={() => admit(entry)} className="flex min-h-12 w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-emerald-50">
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-slate-900">{entry.product.name}</span>
                    <span className="font-mono text-xs text-slate-500">{entry.product.sku}</span>
                  </span>
                  <span className="shrink-0 text-right text-xs text-slate-600">
                    <span className="block font-semibold text-slate-900">{formatMoney(entry.product.selling_price)}</span>
                    <span className="block">{formatQuantity(entry.product.current_stock)} {entry.product.unit_code}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {scanned && <ScannedProductCard product={scanned.product} match={scanned.match} onClose={() => setScanned(null)} />}

      {q !== '' && !choices && (
        <div className="mt-2 rounded-lg border border-slate-200 bg-white shadow-sm">
          {results.isPending && <p className="px-3 py-3 text-sm text-slate-500">{t('common.loading')}</p>}
          {results.isError && <p className="px-3 py-3 text-sm text-red-700">{t('common.loadError')}</p>}
          {results.data?.items.length === 0 && <p className="px-3 py-3 text-sm text-slate-600">{t('sales.form.noProducts')}</p>}
          {results.data && results.data.items.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {results.data.items.map((product) => (
                <li key={product.id}>
                  <button type="button" onClick={() => pick(product)} className="flex min-h-12 w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-emerald-50">
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-slate-900">{product.name}</span>
                      <span className="font-mono text-xs text-slate-500">{product.sku}</span>
                    </span>
                    <span className="shrink-0 text-right text-xs text-slate-600">
                      <span className="block font-semibold text-slate-900">{formatMoney(product.selling_price)}</span>
                      <span className="block">{formatQuantity(product.current_stock)} {product.unit_code}</span>
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
