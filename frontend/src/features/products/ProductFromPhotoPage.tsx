import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, ImagePlus } from 'lucide-react'
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { listCategories, listUnits } from '@/api/catalog'
import { ApiError } from '@/api/client'
import {
  analyzePhoto,
  confirmProductFromPhoto,
  getImageStatus,
  type Analysis,
  type DuplicateMatch,
  type ReviewedProduct,
} from '@/api/imageIntelligence'
import { SelectField, TextField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, Badge, Button, LinkButton, PageHeader, Spinner } from '@/components/ui'
import { useEntitlements } from '@/features/subscription/useEntitlements'
import { needsNotice, useFailure } from '@/hooks/useFailure'
import { useIdempotencyKey } from '@/lib/idempotency'
import {
  ACCEPT_ATTRIBUTE,
  checkImageFile,
  DEFAULT_MAX_BYTES,
  detectBarcodeInImage,
  readImage,
  type EncodedImage,
} from '@/lib/imageFile'

interface Review {
  sku: string
  name: string
  brand: string
  barcode: string
  categoryId: string
  unitId: string
  mrp: string
  sellingPrice: string
}

const EMPTY_REVIEW: Review = { sku: '', name: '', brand: '', barcode: '', categoryId: '', unitId: '', mrp: '', sellingPrice: '' }

/** What the photo suggested, put into the boxes for the person to check. They are suggestions, never facts. */
function reviewFromAnalysis(analysis: Analysis, barcodeText: string): Review {
  const review: Review = { ...EMPTY_REVIEW, barcode: analysis.barcode ?? barcodeText }
  for (const suggestion of analysis.suggestions) {
    if (suggestion.field === 'name') review.name = suggestion.value
    else if (suggestion.field === 'brand') review.brand = suggestion.value
    else if (suggestion.field === 'category' && suggestion.category_id) review.categoryId = String(suggestion.category_id)
    else if (suggestion.field === 'unit' && suggestion.unit_id) review.unitId = String(suggestion.unit_id)
  }
  return review
}

function duplicatesFromError(error: unknown): DuplicateMatch[] | null {
  if (!(error instanceof ApiError) || error.errorCode !== 'possible_duplicate') return null
  const data = error.data as { possible_duplicates?: DuplicateMatch[] } | undefined
  return data?.possible_duplicates ?? []
}

/**
 * Add a product from a photo. The photo is only READ: every result is a suggestion, labelled "Detected" or
 * "Suggested", and nothing is created until the person reviews the boxes and presses "Confirm & Create Product".
 * A photo never changes stock, prices, orders or khata. Image analysis is optional: without it, or when it
 * fails, the person simply fills the boxes by hand.
 */
export function ProductFromPhotoPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { allows } = useEntitlements()
  const status = useQuery({ queryKey: ['imageStatus'], queryFn: getImageStatus, staleTime: 60_000 })
  const categories = useQuery({ queryKey: ['categories'], queryFn: listCategories })
  const units = useQuery({ queryKey: ['units'], queryFn: listUnits })

  const cameraInput = useRef<HTMLInputElement>(null)
  const pickInput = useRef<HTMLInputElement>(null)
  const [image, setImage] = useState<EncodedImage | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [barcodeText, setBarcodeText] = useState('')
  const [barcodeNote, setBarcodeNote] = useState<string | null>(null)
  const [useProvider, setUseProvider] = useState(false)
  const [enrich, setEnrich] = useState(false)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [review, setReview] = useState<Review>(EMPTY_REVIEW)
  const [serverDuplicates, setServerDuplicates] = useState<DuplicateMatch[]>([])
  const [acknowledged, setAcknowledged] = useState(false)
  const [keepPhoto, setKeepPhoto] = useState(false)
  const [errors, setErrors] = useState<Partial<Record<keyof Review, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const analyzeFailure = useFailure()
  const saveFailure = useFailure()
  const idem = useIdempotencyKey()

  const maxBytes = status.data?.max_bytes ?? DEFAULT_MAX_BYTES
  const maxMb = Math.round(maxBytes / (1024 * 1024))

  // The preview is a local address for the chosen file; release it when the photo changes or the page closes.
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = '' // choosing the same file again must still fire
    if (!file) return
    setFileProblem(null)
    const check = checkImageFile(file, maxBytes)
    if (check !== 'ok') {
      setFileProblem(check === 'size' ? t('photo.tooLarge', { mb: maxMb }) : t('photo.invalidType'))
      return
    }
    try {
      const encoded = await readImage(file)
      setImage(encoded)
      setPreviewUrl(URL.createObjectURL(file))
      setAnalysis(null)
      setServerDuplicates([])
      setAcknowledged(false)
      analyzeFailure.clear()
      saveFailure.clear()
      const found = await detectBarcodeInImage(file)
      if (found === 'unsupported') setBarcodeNote(t('photo.barcodeUnsupported'))
      else if (found === null) setBarcodeNote(t('photo.barcodeNone'))
      else {
        setBarcodeText(found)
        setBarcodeNote(t('photo.barcodeFound', { code: found }))
      }
    } catch {
      setFileProblem(t('photo.unreadable'))
    }
  }

  const analyze = useMutation({
    mutationFn: () =>
      analyzePhoto({
        image_base64: image!.base64,
        content_type: image!.contentType || null,
        barcode_hint: barcodeText.trim() || null,
        use_provider: useProvider,
        enrich,
      }),
    onSuccess: (result) => {
      setAnalysis(result)
      // Suggestions fill only the empty boxes: what the person already typed is never overwritten.
      const suggested = reviewFromAnalysis(result, barcodeText.trim())
      setReview((current) => Object.fromEntries(
        (Object.keys(current) as (keyof Review)[]).map((key) => [key, current[key] === '' ? suggested[key] : current[key]]),
      ) as unknown as Review)
    },
    onError: (error) => {
      if (error instanceof ApiError && !needsNotice(error)) setFileProblem(error.message)
      else analyzeFailure.setFailure(error)
    },
  })

  const confirm = useMutation({
    mutationFn: () => {
      const product: ReviewedProduct = {
        sku: review.sku.trim(),
        name: review.name.trim(),
        brand: review.brand.trim() || null,
        category_id: Number(review.categoryId),
        unit_id: Number(review.unitId),
        default_supplier_id: null,
        reorder_level: '0',
        mrp: review.mrp.trim() || null,
        selling_price: review.sellingPrice.trim(),
        purchase_price: null,
        barcode: review.barcode.trim() || null,
      }
      const payload = {
        product,
        acknowledge_duplicates: acknowledged,
        keep_image: keepPhoto,
        image_base64: keepPhoto ? image!.base64 : null,
        content_type: keepPhoto ? image!.contentType || null : null,
      }
      // The key makes a repeat of this exact confirmation return the same product, never a second one.
      const fingerprint = JSON.stringify({ ...payload, image_base64: payload.image_base64?.length ?? 0 })
      return confirmProductFromPhoto(payload, { idempotencyKey: idem.keyFor(fingerprint) })
    },
    onSuccess: async (result) => {
      idem.renew()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['products'] }),
        queryClient.invalidateQueries({ queryKey: ['inventory'] }),
      ])
      void navigate(`/products/${result.product.id}`, { state: { warnings: result.warnings } })
    },
    onError: (error) => {
      const duplicates = duplicatesFromError(error)
      if (duplicates !== null) {
        setServerDuplicates(duplicates)
        setFormError(error instanceof ApiError ? error.message : null)
        return
      }
      if (error instanceof ApiError && !needsNotice(error)) {
        const mapped: Partial<Record<keyof Review, string>> = {}
        const fields: Record<string, keyof Review> = {
          sku: 'sku',
          name: 'name',
          brand: 'brand',
          barcode: 'barcode',
          category_id: 'categoryId',
          unit_id: 'unitId',
          mrp: 'mrp',
          selling_price: 'sellingPrice',
        }
        for (const [apiField, message] of Object.entries(error.fieldErrors)) {
          if (fields[apiField]) mapped[fields[apiField]] = message
        }
        setErrors(mapped)
        setFormError(Object.keys(mapped).length === 0 ? error.message : t('products.form.fixErrors'))
      } else saveFailure.setFailure(error)
    },
  })

  function set(changes: Partial<Review>) {
    setReview((current) => ({ ...current, ...changes }))
    setErrors((current) => ({ ...current, ...Object.fromEntries(Object.keys(changes).map((key) => [key, undefined])) }))
  }

  function submit() {
    setFormError(null)
    saveFailure.clear()
    const next: Partial<Record<keyof Review, string>> = {}
    if (review.sku.trim() === '') next.sku = t('photo.needsSku')
    if (review.name.trim() === '') next.name = t('photo.needsName')
    if (review.categoryId === '') next.categoryId = t('photo.needsCategory')
    if (review.unitId === '') next.unitId = t('photo.needsUnit')
    if (review.sellingPrice.trim() === '') next.sellingPrice = t('photo.needsPrice')
    setErrors(next)
    if (Object.keys(next).length > 0) return
    confirm.mutate()
  }

  if (allows('image_intelligence') === false) {
    return (
      <div className="space-y-6">
        <PageHeader title={t('photo.title')} />
        <Alert tone="warning">{t('photo.planNeeded')}</Alert>
        <LinkButton to="/products/new" variant="secondary">
          {t('products.add')}
        </LinkButton>
      </div>
    )
  }
  if (categories.isPending || units.isPending) return <Spinner />

  const duplicates = serverDuplicates.length > 0 ? serverDuplicates : (analysis?.possible_duplicates ?? [])
  const exactDuplicate = duplicates.some((match) => match.strength === 'EXACT')
  const existing = analysis?.existing_products ?? []
  const blocked = exactDuplicate || existing.length > 0
  const needsAck = duplicates.length > 0 && !exactDuplicate

  return (
    <div className="space-y-6">
      <PageHeader title={t('photo.title')} subtitle={t('photo.subtitle')} />
      <p className="text-sm text-slate-600">{t('photo.optional')}</p>

      <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap gap-3">
          <input ref={cameraInput} type="file" accept={ACCEPT_ATTRIBUTE} capture="environment" className="sr-only" tabIndex={-1} onChange={(e) => void onFile(e)} />
          <input ref={pickInput} type="file" accept={ACCEPT_ATTRIBUTE} className="sr-only" tabIndex={-1} onChange={(e) => void onFile(e)} />
          <Button variant={image ? 'secondary' : 'primary'} onClick={() => cameraInput.current?.click()}>
            <Camera aria-hidden="true" className="size-5" />
            {t('photo.take')}
          </Button>
          <Button variant="secondary" onClick={() => pickInput.current?.click()}>
            <ImagePlus aria-hidden="true" className="size-5" />
            {image ? t('photo.change') : t('photo.upload')}
          </Button>
        </div>
        <p className="text-xs text-slate-500">{t('photo.formats', { mb: maxMb })}</p>
        {fileProblem && (
          <p role="alert" className="text-sm font-medium text-red-700">
            {fileProblem}
          </p>
        )}
        {previewUrl && <img src={previewUrl} alt={t('photo.preview')} className="max-h-72 rounded-lg border border-slate-200 object-contain" />}
      </section>

      {image && (
        <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('photo.options')}</h2>
          {barcodeNote && <p className="text-sm text-slate-600">{barcodeNote}</p>}
          <TextField label={t('photo.barcodeLabel')} value={barcodeText} inputMode="numeric" maxLength={50} onChange={(e) => setBarcodeText(e.target.value)} optional />
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              className="mt-1 size-5"
              checked={useProvider}
              disabled={status.data?.configured === false}
              onChange={(e) => setUseProvider(e.target.checked)}
            />
            <span>
              <span className="font-medium text-slate-800">{t('photo.useProvider')}</span>
              <span className="block text-sm text-slate-600">
                {status.data?.configured === false ? t('photo.providerNotConfigured') : t('photo.useProviderHint')}
              </span>
            </span>
          </label>
          <label className="flex items-start gap-3">
            <input type="checkbox" className="mt-1 size-5" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} />
            <span>
              <span className="font-medium text-slate-800">{t('photo.enrich')}</span>
              <span className="block text-sm text-slate-600">{t('photo.enrichHint')}</span>
            </span>
          </label>
          <Button loading={analyze.isPending} onClick={() => {
            analyzeFailure.clear()
            setFileProblem(null)
            analyze.mutate()
          }}>
            {analyze.isPending ? t('photo.analyzing') : t('photo.analyze')}
          </Button>
          {analyzeFailure.failure !== null && (
            <ErrorNotice
              error={analyzeFailure.failure}
              context="image_upload"
              safeToRepeat
              retry={() => {
                analyzeFailure.clear()
                analyze.mutate()
              }}
              choose_another={() => pickInput.current?.click()}
            />
          )}
        </section>
      )}

      {analysis && (
        <>
          <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">{t('photo.results')}</h2>
            <Alert tone="info">{t('photo.notConfirmed')}</Alert>
            {analysis.message && <Alert tone="warning">{analysis.message}</Alert>}
            {analysis.status === 'PROVIDER_FAILED' && <Alert tone="warning">{t('photo.analysisFailed')}</Alert>}
            <p className="text-sm text-slate-600">
              {analysis.sent_to_provider ? t('photo.sentToService', { service: analysis.provider_label ?? '' }) : t('photo.notSent')} {t('photo.noChange')}
            </p>
            {analysis.suggestions.length === 0 && analysis.visible_text.length === 0 && <p className="text-slate-600">{t('photo.nothingFound')}</p>}
            {analysis.suggestions.length > 0 && (
              <ul className="divide-y divide-slate-100">
                {analysis.suggestions.map((suggestion, index) => (
                  <li key={`${suggestion.field}-${index}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <span>
                      <span className="text-sm text-slate-500">{t(`photo.fields.${suggestion.field}` as 'photo.fields.name', { defaultValue: suggestion.field })}: </span>
                      <span className="font-medium text-slate-900">{suggestion.value}</span>
                    </span>
                    <Badge tone={suggestion.label === 'Detected' ? 'green' : 'amber'}>
                      {suggestion.label === 'Detected' ? t('photo.detected') : t('photo.suggested')}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
            {analysis.visible_text.length > 0 && (
              <details className="text-sm text-slate-600">
                <summary className="cursor-pointer font-medium">{t('photo.fields.visible_text')}</summary>
                <ul className="mt-1 list-disc pl-5">
                  {analysis.visible_text.map((line, index) => (
                    <li key={index}>{line}</li>
                  ))}
                </ul>
              </details>
            )}
            {analysis.notes.map((note) => (
              <p key={note} className="text-sm text-slate-600">
                {note}
              </p>
            ))}
          </section>

          {existing.length > 0 && (
            <Alert tone="warning">
              <p className="font-semibold">{t('photo.existing')}</p>
              <ul className="mt-1">
                {existing.map((product) => (
                  <li key={product.id}>
                    {product.name} <span className="font-mono text-xs">({product.sku})</span>{' '}
                    <Link to={`/products/${product.id}`} className="font-medium underline">
                      {t('photo.open')}
                    </Link>
                  </li>
                ))}
              </ul>
            </Alert>
          )}
          {duplicates.length > 0 && (
            <Alert tone="warning">
              <p className="font-semibold">{t('photo.duplicates')}</p>
              <p className="text-sm">{t('photo.duplicatesHint')}</p>
              <ul className="mt-2 space-y-1">
                {duplicates.map((match) => (
                  <li key={match.product_id}>
                    <Link to={`/products/${match.product_id}`} className="font-medium underline">
                      {match.name}
                    </Link>{' '}
                    <span className="font-mono text-xs">({match.sku})</span> — {t(`photo.strength.${match.strength}`)}
                    <span className="block text-xs text-slate-600">{match.reasons.join(', ')}</span>
                  </li>
                ))}
              </ul>
              {exactDuplicate && <p className="mt-2 font-medium">{t('photo.exactBlocked')}</p>}
            </Alert>
          )}

          <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">{t('photo.review')}</h2>
              <p className="text-sm text-slate-600">{t('photo.reviewHint')}</p>
            </div>
            {formError && <Alert tone="error">{formError}</Alert>}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <TextField label={t('photo.sku')} value={review.sku} onChange={(e) => set({ sku: e.target.value })} error={errors.sku} maxLength={50} />
              <TextField label={t('photo.name')} value={review.name} onChange={(e) => set({ name: e.target.value })} error={errors.name} maxLength={200} />
              <TextField label={t('photo.brand')} value={review.brand} onChange={(e) => set({ brand: e.target.value })} error={errors.brand} maxLength={100} optional />
              <TextField label={t('photo.fields.barcode')} value={review.barcode} inputMode="numeric" onChange={(e) => set({ barcode: e.target.value })} error={errors.barcode} maxLength={50} optional />
              <SelectField label={t('photo.category')} value={review.categoryId} onChange={(e) => set({ categoryId: e.target.value })} error={errors.categoryId}>
                <option value="">{t('products.form.choose')}</option>
                {categories.data?.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </SelectField>
              <SelectField label={t('photo.unit')} value={review.unitId} onChange={(e) => set({ unitId: e.target.value })} error={errors.unitId}>
                <option value="">{t('products.form.choose')}</option>
                {units.data?.map((unit) => (
                  <option key={unit.id} value={unit.id}>
                    {unit.name} ({unit.code})
                  </option>
                ))}
              </SelectField>
              <TextField label={t('photo.mrp')} value={review.mrp} inputMode="decimal" onChange={(e) => set({ mrp: e.target.value })} error={errors.mrp} optional />
              <TextField label={t('photo.sellingPrice')} value={review.sellingPrice} inputMode="decimal" onChange={(e) => set({ sellingPrice: e.target.value })} error={errors.sellingPrice} />
            </div>
            {needsAck && (
              <label className="flex items-start gap-3">
                <input type="checkbox" className="mt-1 size-5" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
                <span className="font-medium text-slate-800">{t('photo.differentProduct')}</span>
              </label>
            )}
            <label className="flex items-start gap-3">
              <input type="checkbox" className="mt-1 size-5" checked={keepPhoto} onChange={(e) => setKeepPhoto(e.target.checked)} />
              <span>
                <span className="font-medium text-slate-800">{t('photo.keepPhoto')}</span>
                <span className="block text-sm text-slate-600">{t('photo.keepHint')}</span>
              </span>
            </label>
            {saveFailure.failure !== null && (
              <ErrorNotice
                error={saveFailure.failure}
                context="save"
                safeToRepeat
                retry={submit}
                cancel={() => void navigate('/products')}
              />
            )}
            <div className="flex flex-wrap gap-3">
              <Button loading={confirm.isPending} disabled={blocked || (needsAck && !acknowledged)} onClick={submit}>
                {confirm.isPending ? t('photo.confirming') : t('photo.confirm')}
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  setAnalysis(null)
                  setErrors({})
                  setFormError(null)
                }}
              >
                {t('photo.edit')}
              </Button>
              <LinkButton to="/products" variant="secondary">
                {t('photo.cancel')}
              </LinkButton>
            </div>
          </section>
        </>
      )}
    </div>
  )
}
