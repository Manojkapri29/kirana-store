import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { createCategory, getShop, getShopTemplate, listCategories, listUnits } from '@/api/catalog'
import { ApiError } from '@/api/client'
import { getProductHistory } from '@/api/inventory'
import { listSupplierOptions } from '@/api/suppliers'
import { createProduct, getProduct, updateProduct } from '@/api/products'
import type { Category, Product, Shop, ShopTemplate, SupplierOption, Unit } from '@/api/types'
import { SelectField, TextField } from '@/components/fields'
import { buttonClasses } from '@/components/buttonStyles'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { CURRENCY_SYMBOL, formatMoney } from '@/lib/format'

import {
  API_FIELD_TO_FORM_FIELD,
  buildCreatePayload,
  buildUpdatePayload,
  emptyValues,
  sellingAboveMrp,
  valuesFromProduct,
  validate,
  type ErrorCode,
  type FieldErrors,
  type FieldName,
  type FormValues,
} from './productForm'

const NEW_CATEGORY = '__new__'

export function ProductFormPage({ mode }: { mode: 'create' | 'edit' }) {
  const { t } = useTranslation()
  const params = useParams()
  const productId = Number(params.id)
  const validId = mode === 'create' || (Number.isInteger(productId) && productId > 0)

  const categories = useQuery({ queryKey: ['categories'], queryFn: listCategories })
  const units = useQuery({ queryKey: ['units'], queryFn: listUnits })
  const shop = useQuery({ queryKey: ['shop'], queryFn: getShop })
  // Suggestions for this kind of business. If they cannot be loaded, the form works without them.
  const template = useQuery({ queryKey: ['shopTemplate'], queryFn: getShopTemplate })
  // Active suppliers for the default-supplier picker. Optional: the form works without them.
  const suppliers = useQuery({ queryKey: ['supplierOptions'], queryFn: listSupplierOptions })
  const product = useQuery({
    queryKey: ['product', productId],
    queryFn: () => getProduct(productId),
    enabled: mode === 'edit' && validId,
  })
  // The unit is locked once stock exists. Any history row means stock exists.
  const history = useQuery({
    queryKey: ['history', productId, 'exists'],
    queryFn: () => getProductHistory(productId, { limit: 1 }),
    enabled: mode === 'edit' && validId,
  })

  const title = mode === 'create' ? t('products.form.addTitle') : t('products.form.editTitle')
  const queries = [categories, units, shop, ...(mode === 'edit' ? [product, history] : [])]

  if (!validId || queries.some((query) => query.isError)) {
    const notFound = !validId || (product.error instanceof ApiError && product.error.status === 404)
    return (
      <div className="space-y-6">
        <PageHeader title={title} />
        {notFound ? (
          <Alert tone="error">{t('products.detail.notFound')}</Alert>
        ) : (
          <QueryError onRetry={() => queries.forEach((query) => void query.refetch())} />
        )}
        <LinkButton to="/products" variant="secondary">
          {t('products.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (queries.some((query) => query.isPending) || !categories.data || !units.data || !shop.data) {
    return <Spinner />
  }
  if (mode === 'edit' && (!product.data || !history.data)) return <Spinner />

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title={title} />
      <ProductForm
        mode={mode}
        product={product.data}
        categories={categories.data}
        units={units.data}
        shop={shop.data}
        template={template.data}
        suppliers={suppliers.data ?? []}
        unitLocked={mode === 'edit' && (history.data?.total ?? 0) > 0}
      />
    </div>
  )
}

interface ProductFormProps {
  mode: 'create' | 'edit'
  product: Product | undefined
  categories: Category[]
  units: Unit[]
  shop: Shop
  template: ShopTemplate | undefined
  suppliers: SupplierOption[]
  unitLocked: boolean
}

function ProductForm({
  mode,
  product,
  categories,
  units,
  shop,
  template,
  suppliers,
  unitLocked,
}: ProductFormProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [values, setValues] = useState<FormValues>(() => (product ? valuesFromProduct(product) : emptyValues()))
  const [clientErrors, setClientErrors] = useState<FieldErrors>({})
  const [serverErrors, setServerErrors] = useState<Partial<Record<FieldName, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [addingCategory, setAddingCategory] = useState(false)
  const [newCategoryName, setNewCategoryName] = useState('')

  const unit = units.find((candidate) => String(candidate.id) === values.unitId)
  // Units the business type suggests come first. Every other unit stays available: no lock-in.
  const suggestedCodes = template?.unit_codes ?? []
  const suggestedUnits = suggestedCodes.flatMap((code) => units.filter((candidate) => candidate.code === code))
  const otherUnits = units.filter((candidate) => !suggestedCodes.includes(candidate.code))
  const unitOption = (candidate: Unit) => (
    <option key={candidate.id} value={candidate.id}>
      {candidate.name} ({candidate.code})
    </option>
  )
  // A product that already uses a since-deactivated supplier keeps it visible and selectable, so editing
  // other details never silently drops the link. A different inactive supplier cannot be newly chosen.
  const currentSupplier =
    product?.default_supplier_id != null && !suppliers.some((s) => s.id === product.default_supplier_id)
      ? { id: product.default_supplier_id, name: product.default_supplier_name ?? String(product.default_supplier_id) }
      : null
  const supplierOptions = currentSupplier ? [currentSupplier, ...suppliers] : suppliers
  const missingSuggestions = (template?.categories ?? []).filter((suggestion) => !suggestion.exists)
  const aboveMrp = sellingAboveMrp(values)
  const blockAboveMrp = aboveMrp && shop.mrp_validation_mode === 'BLOCK'

  function setField(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
    // Editing a field clears its old error; the next submit re-checks everything.
    setClientErrors((current) => ({ ...current, [field]: undefined }))
    setServerErrors((current) => ({ ...current, [field]: undefined }))
  }

  function errorFor(field: FieldName): string | undefined {
    const client = clientErrors[field]
    if (client) return t(`products.form.validation.${client.code satisfies ErrorCode}`, client.params)
    return serverErrors[field]
  }

  const addCategory = useMutation({
    mutationFn: (name: string) => createCategory(name.trim()),
    onSuccess: async (category) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['categories'] }),
        queryClient.invalidateQueries({ queryKey: ['shopTemplate'] }),
      ])
      setField('categoryId', String(category.id))
      setAddingCategory(false)
      setNewCategoryName('')
    },
    onError: (error) =>
      setServerErrors((current) => ({
        ...current,
        categoryId: error instanceof ApiError ? error.message : t('common.genericError'),
      })),
  })

  const save = useMutation({
    mutationFn: async () => {
      if (mode === 'create') return createProduct(buildCreatePayload(values))
      const changes = buildUpdatePayload(values, product!)
      // Nothing changed: no request needed.
      if (Object.keys(changes).length === 0) return { product: product!, warnings: [] as string[] }
      return updateProduct(product!.id, changes)
    },
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['products'] }),
        queryClient.invalidateQueries({ queryKey: ['inventory'] }),
        queryClient.invalidateQueries({ queryKey: ['product', result.product.id] }),
        queryClient.invalidateQueries({ queryKey: ['history', result.product.id] }),
      ])
      void navigate(`/products/${result.product.id}`, { state: { warnings: result.warnings } })
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        const mapped: Partial<Record<FieldName, string>> = {}
        for (const [apiField, message] of Object.entries(error.fieldErrors)) {
          const field = API_FIELD_TO_FORM_FIELD[apiField]
          if (field) mapped[field] = message
        }
        setServerErrors(mapped)
        // Show the message at the top too when it is not tied to a visible field.
        setFormError(Object.keys(mapped).length === 0 ? error.message : t('products.form.fixErrors'))
      } else {
        setFormError(t('common.genericError'))
      }
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setServerErrors({})
    const errors = validate(values, unit, mode)
    setClientErrors(errors)
    if (Object.keys(errors).length > 0 || blockAboveMrp) {
      setFormError(t('products.form.fixErrors'))
      return
    }
    save.mutate()
  }

  const unitSuffix = unit?.code

  return (
    <form onSubmit={submit} noValidate className="space-y-6">
      {formError && <Alert tone="error">{formError}</Alert>}

      <Section title={t('products.form.basicSection')}>
        <TextField
          label={t('products.form.fields.sku')}
          hint={t('products.form.hints.sku')}
          value={values.sku}
          onChange={(event) => setField('sku', event.target.value)}
          error={errorFor('sku')}
          autoCapitalize="characters"
          maxLength={50}
        />
        <TextField
          label={t('products.form.fields.name')}
          value={values.name}
          onChange={(event) => setField('name', event.target.value)}
          error={errorFor('name')}
          maxLength={200}
        />
        <TextField
          label={t('products.form.fields.brand')}
          optional
          value={values.brand}
          onChange={(event) => setField('brand', event.target.value)}
          error={errorFor('brand')}
          maxLength={100}
        />
        <div className="space-y-3">
          <SelectField
            label={t('products.form.fields.category')}
            value={addingCategory ? NEW_CATEGORY : values.categoryId}
            onChange={(event) => {
              if (event.target.value === NEW_CATEGORY) setAddingCategory(true)
              else {
                setAddingCategory(false)
                setField('categoryId', event.target.value)
              }
            }}
            error={errorFor('categoryId')}
          >
            <option value="">{t('products.form.choose')}</option>
            {categories
              .filter((category) => category.is_active || String(category.id) === values.categoryId)
              .map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
            <option value={NEW_CATEGORY}>{t('products.form.newCategory')}</option>
          </SelectField>
          {missingSuggestions.length > 0 && !addingCategory && (
            <div>
              <p className="mb-1.5 text-sm text-slate-500">{t('products.form.suggestedCategories')}</p>
              <div className="flex flex-wrap gap-2">
                {missingSuggestions.slice(0, 6).map((suggestion) => (
                  <button
                    key={suggestion.name}
                    type="button"
                    disabled={addCategory.isPending}
                    onClick={() => addCategory.mutate(suggestion.name)}
                    className="min-h-10 rounded-full border border-slate-300 bg-white px-3 text-sm text-slate-700 hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-emerald-600 disabled:opacity-60"
                  >
                    + {suggestion.name}
                  </button>
                ))}
              </div>
            </div>
          )}
          {addingCategory && (
            <div className="flex gap-2">
              <div className="flex-1">
                <TextField
                  label={t('products.form.newCategoryName')}
                  value={newCategoryName}
                  onChange={(event) => setNewCategoryName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      if (newCategoryName.trim()) addCategory.mutate(newCategoryName)
                    }
                  }}
                  maxLength={100}
                  autoFocus
                />
              </div>
              <div className="self-end">
                <Button
                  loading={addCategory.isPending}
                  disabled={!newCategoryName.trim()}
                  onClick={() => addCategory.mutate(newCategoryName)}
                >
                  {t('products.form.addCategory')}
                </Button>
              </div>
            </div>
          )}
        </div>
        <SelectField
          label={t('products.form.fields.unit')}
          value={values.unitId}
          onChange={(event) => setField('unitId', event.target.value)}
          error={errorFor('unitId')}
          hint={unitLocked ? t('products.form.hints.unitLocked') : undefined}
          disabled={unitLocked}
        >
          <option value="">{t('products.form.choose')}</option>
          {suggestedUnits.length > 0 ? (
            <>
              <optgroup label={t('products.form.suggestedUnits')}>{suggestedUnits.map(unitOption)}</optgroup>
              <optgroup label={t('products.form.otherUnits')}>{otherUnits.map(unitOption)}</optgroup>
            </>
          ) : (
            units.map(unitOption)
          )}
        </SelectField>
        <TextField
          label={t('products.form.fields.barcode')}
          hint={t('products.form.hints.barcode')}
          optional
          value={values.barcode}
          onChange={(event) => setField('barcode', event.target.value)}
          // A barcode scanner presses Enter after the code. That must not submit the form.
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.preventDefault()
          }}
          error={errorFor('barcode')}
          inputMode="numeric"
          maxLength={50}
        />
        <SelectField
          label={t('products.form.fields.defaultSupplier')}
          hint={t('products.form.hints.defaultSupplier')}
          optional
          value={values.supplierId}
          onChange={(event) => setField('supplierId', event.target.value)}
          error={errorFor('supplierId')}
        >
          <option value="">{t('products.form.noSupplier')}</option>
          {supplierOptions.map((supplier) => (
            <option key={supplier.id} value={supplier.id}>
              {supplier.name}
            </option>
          ))}
        </SelectField>
        <TextField
          label={t('products.form.fields.reorderLevel')}
          hint={t('products.form.hints.reorderLevel')}
          value={values.reorderLevel}
          onChange={(event) => setField('reorderLevel', event.target.value)}
          error={errorFor('reorderLevel')}
          inputMode="decimal"
          suffix={unitSuffix}
        />
      </Section>

      <Section title={t('products.form.priceSection')}>
        <TextField
          label={t('products.form.fields.mrp')}
          hint={t('products.form.hints.mrp')}
          optional
          value={values.mrp}
          onChange={(event) => setField('mrp', event.target.value)}
          error={errorFor('mrp')}
          inputMode="decimal"
          prefix={CURRENCY_SYMBOL}
        />
        <TextField
          label={t('products.form.fields.sellingPrice')}
          value={values.sellingPrice}
          onChange={(event) => setField('sellingPrice', event.target.value)}
          error={errorFor('sellingPrice') ?? (blockAboveMrp ? t('products.form.validation.aboveMrpBlock') : undefined)}
          inputMode="decimal"
          prefix={CURRENCY_SYMBOL}
        />
        <TextField
          label={t('products.form.fields.purchasePrice')}
          hint={t('products.form.hints.purchasePrice')}
          optional
          value={values.purchasePrice}
          onChange={(event) => setField('purchasePrice', event.target.value)}
          error={errorFor('purchasePrice')}
          inputMode="decimal"
          prefix={CURRENCY_SYMBOL}
        />
        {mode === 'edit' && product && (
          <TextField
            label={t('products.form.fields.avgCost')}
            hint={t('products.form.hints.avgCost')}
            value={formatMoney(product.avg_cost)}
            readOnly
            disabled
          />
        )}
        {aboveMrp && !blockAboveMrp && (
          <div className="sm:col-span-2">
            <Alert tone="warning">{t('products.form.validation.aboveMrpWarn')}</Alert>
          </div>
        )}
      </Section>

      {mode === 'create' && (
        <Section title={t('products.form.stockSection')} hint={t('products.form.stockSectionHint')}>
          <TextField
            label={t('products.form.fields.openingStock')}
            hint={t('products.form.hints.openingStock')}
            optional
            value={values.openingStock}
            onChange={(event) => setField('openingStock', event.target.value)}
            error={errorFor('openingStock')}
            inputMode="decimal"
            suffix={unitSuffix}
          />
          <TextField
            label={t('products.form.fields.openingStockCost')}
            hint={t('products.form.hints.openingStockCost')}
            optional
            value={values.openingStockCost}
            onChange={(event) => setField('openingStockCost', event.target.value)}
            error={errorFor('openingStockCost')}
            inputMode="decimal"
            prefix={CURRENCY_SYMBOL}
          />
        </Section>
      )}

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Link
          to={product ? `/products/${product.id}` : '/products'}
          className={buttonClasses('secondary')}
        >
          {t('common.cancel')}
        </Link>
        <Button type="submit" loading={save.isPending}>
          {save.isPending ? t('common.saving') : mode === 'create' ? t('products.form.submitAdd') : t('products.form.submitEdit')}
        </Button>
      </div>
    </form>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      {hint && <p className="mt-1 text-sm text-slate-500">{hint}</p>}
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">{children}</div>
    </section>
  )
}
