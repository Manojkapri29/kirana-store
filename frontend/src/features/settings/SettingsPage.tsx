import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  createCategory,
  getShop,
  getShopTemplate,
  listBusinessTypes,
  updateShopBusinessType,
} from '@/api/catalog'
import { ApiError } from '@/api/client'
import type { BusinessType, Shop } from '@/api/types'
import { SelectField, TextField } from '@/components/fields'
import { Alert, Button, PageHeader, QueryError, Spinner } from '@/components/ui'
import { useBusinessTypeLabel } from '@/hooks/useBusinessTypeLabel'

export function SettingsPage() {
  const { t } = useTranslation()
  const shop = useQuery({ queryKey: ['shop'], queryFn: getShop })
  const types = useQuery({ queryKey: ['businessTypes'], queryFn: listBusinessTypes })

  if (shop.isError || types.isError) {
    return (
      <QueryError
        onRetry={() => {
          void shop.refetch()
          void types.refetch()
        }}
      />
    )
  }
  if (!shop.data || !types.data) return <Spinner />

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title={t('settings.title')} subtitle={t('settings.subtitle')} />
      <BusinessCard shop={shop.data} types={types.data} />
      <SuggestionsCard />
      <DetailsCard shop={shop.data} />
    </div>
  )
}

function BusinessCard({ shop, types }: { shop: Shop; types: BusinessType[] }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const label = useBusinessTypeLabel()
  const [selected, setSelected] = useState(shop.business_type)

  const save = useMutation({
    mutationFn: () => updateShopBusinessType(selected),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['shop'] }),
        queryClient.invalidateQueries({ queryKey: ['shopTemplate'] }),
      ])
    },
  })

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{t('settings.businessSection')}</h2>
      <TextField label={t('settings.businessName')} value={shop.name} readOnly disabled />
      <SelectField
        label={t('settings.businessType')}
        hint={t('settings.businessTypeHint')}
        value={selected}
        onChange={(event) => {
          setSelected(event.target.value)
          save.reset()
        }}
      >
        {types.map((type) => (
          <option key={type.code} value={type.code}>
            {label(type.code, type.name)}
          </option>
        ))}
      </SelectField>
      {save.isError && (
        <Alert tone="error">{save.error instanceof ApiError ? save.error.message : t('common.genericError')}</Alert>
      )}
      {save.isSuccess && <Alert tone="success">{t('settings.saved')}</Alert>}
      <Button loading={save.isPending} disabled={selected === shop.business_type} onClick={() => save.mutate()}>
        {save.isPending ? t('common.saving') : t('common.save')}
      </Button>
    </section>
  )
}

/** Ready-made categories for the shop's business type. Nothing is created unless the owner asks for it. */
function SuggestionsCard() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const template = useQuery({ queryKey: ['shopTemplate'], queryFn: getShopTemplate })

  const addAll = useMutation({
    mutationFn: async (names: string[]) => {
      for (const name of names) await createCategory(name)
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['categories'] }),
        queryClient.invalidateQueries({ queryKey: ['shopTemplate'] }),
      ])
    },
  })

  if (template.isError) return <QueryError onRetry={() => void template.refetch()} />
  if (!template.data) return null

  const categories = template.data.categories
  const missing = categories.filter((category) => !category.exists).map((category) => category.name)

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{t('settings.suggestionsSection')}</h2>
      {categories.length === 0 ? (
        <p className="text-slate-600">{t('settings.noSuggestions')}</p>
      ) : (
        <>
          <p className="text-sm text-slate-600">{t('settings.suggestionsHint')}</p>
          <ul className="flex flex-wrap gap-2">
            {categories.map((category) => (
              <li
                key={category.name}
                className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm ${
                  category.exists ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-slate-300 text-slate-700'
                }`}
              >
                {category.exists && <Check aria-hidden="true" className="size-4" />}
                {category.name}
                {category.exists && <span className="sr-only">({t('settings.alreadyAdded')})</span>}
              </li>
            ))}
          </ul>
          {addAll.isError && <Alert tone="error">{t('common.genericError')}</Alert>}
          <Button
            variant="secondary"
            loading={addAll.isPending}
            disabled={missing.length === 0}
            onClick={() => addAll.mutate(missing)}
          >
            {t('settings.addAllMissing')}
          </Button>
        </>
      )}
    </section>
  )
}

function DetailsCard({ shop }: { shop: Shop }) {
  const { t } = useTranslation()
  const rows: [string, string][] = [
    [t('settings.timezone'), shop.timezone],
    [t('settings.language'), shop.language === 'hi' ? 'हिन्दी' : 'English'],
    [t('settings.mrpMode'), shop.mrp_validation_mode === 'BLOCK' ? t('settings.mrpBlock') : t('settings.mrpWarn')],
    [t('settings.negativeStock'), shop.allow_negative_stock ? t('settings.yes') : t('settings.no')],
  ]
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{t('settings.detailsSection')}</h2>
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        {rows.map(([name, value]) => (
          <div key={name}>
            <dt className="text-sm text-slate-500">{name}</dt>
            <dd className="font-medium text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
