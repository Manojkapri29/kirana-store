import { useMutation } from '@tanstack/react-query'
import { Camera, ImagePlus } from 'lucide-react'
import { useRef, useState, type ChangeEvent } from 'react'
import { useTranslation } from 'react-i18next'

import {
  extractDocument,
  matchDocument,
  proposeAction,
  type AiAction,
  type DocumentKind,
  type DocumentRow,
  type MatchedRow,
  type MatchResult,
} from '@/api/ai'
import { ApiError } from '@/api/client'
import { TextField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, Badge, Button } from '@/components/ui'
import { ACCEPT_ATTRIBUTE, checkImageFile, readImage, type EncodedImage } from '@/lib/imageFile'

import { ActionPreviewCard } from './ActionPreviewCard'

const EMPTY_ROW: DocumentRow = {
  name: '', brand: null, barcode: '', sku: null, quantity: '', unit: null, unit_price: '', discount: '', line_total: null, product_id: null,
}
const TONE = { MATCHED: 'green', POSSIBLE_MATCH: 'amber', NEW_PRODUCT_CANDIDATE: 'slate' } as const

/**
 * A photographed invoice or counted stock list becomes a DRAFT: read, checked line by line, matched to your products
 * ("Matched", "Possible Match", "New Product Candidate"), shown for review, and only then prepared as an action that
 * needs a confirmation. The photo is not stored; text printed on it is only ever copied, never followed. Without an AI
 * provider the lines can still be typed by hand and go through exactly the same checks.
 */
export function DocumentsPanel({ allowed, configured }: { allowed: boolean; configured: boolean }) {
  const { t } = useTranslation()
  const camera = useRef<HTMLInputElement>(null)
  const pick = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState<DocumentKind>('invoice')
  const [image, setImage] = useState<EncodedImage | null>(null)
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [header, setHeader] = useState<Record<string, string>>({ supplier: '', invoice_no: '', invoice_date: '', total: '' })
  const [rows, setRows] = useState<DocumentRow[]>([{ ...EMPTY_ROW }])
  const [warnings, setWarnings] = useState<string[]>([])
  const [notConfigured, setNotConfigured] = useState<string | null>(null)
  const [result, setResult] = useState<MatchResult | null>(null)
  const [action, setAction] = useState<AiAction | null>(null)

  const read = useMutation({
    mutationFn: () => extractDocument(kind, image!.base64, image!.contentType || null),
    onSuccess: (data) => {
      setResult(null)
      setAction(null)
      setWarnings(data.warnings)
      setNotConfigured(data.status === 'NOT_CONFIGURED' ? data.message : null)
      if (data.status === 'OK') {
        setHeader({ supplier: data.header.supplier ?? '', invoice_no: data.header.invoice_no ?? '', invoice_date: data.header.invoice_date ?? '', total: data.header.total ?? '' })
        setRows(data.rows.length > 0 ? data.rows.map((r) => ({ ...EMPTY_ROW, ...r })) : [{ ...EMPTY_ROW }])
      }
    },
  })
  const check = useMutation({
    mutationFn: (payload: { header: Record<string, string>; rows: DocumentRow[] }) => matchDocument(kind, payload.header, payload.rows),
    onSuccess: setResult,
  })
  const prepare = useMutation({
    mutationFn: (matched: MatchResult) => {
      if (kind === 'invoice') {
        return proposeAction('PURCHASE_DRAFT', 'invoice_photo', {
          supplier_id: matched.header.supplier_id,
          supplier_invoice_no: matched.header.invoice_no || null,
          purchase_date: matched.header.invoice_date || null,
          items: matched.rows.map((r) => ({ product_id: r.product_id, quantity: r.quantity, unit_cost: r.unit_price, discount: r.discount || null })),
        })
      }
      return proposeAction('STOCK_ADJUSTMENT', 'stock_list_photo', {
        items: matched.rows.map((r) => ({ product_id: r.product_id, counted_quantity: r.quantity, system_quantity: r.system_quantity })),
      })
    },
    onSuccess: setAction,
  })

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setFileProblem(null)
    if (checkImageFile(file) !== 'ok') {
      setFileProblem(t('photo.invalidType'))
      return
    }
    try {
      setImage(await readImage(file))
    } catch {
      setFileProblem(t('photo.unreadable'))
    }
  }

  function edit(index: number, changes: Partial<DocumentRow>) {
    setRows((all) => all.map((row, i) => (i === index ? { ...row, ...changes } : row)))
    setResult(null) // what was checked is no longer what is on screen
    setAction(null)
  }
  const runCheck = (nextRows: DocumentRow[] = rows, nextHeader = header) => check.mutate({ header: nextHeader, rows: nextRows })
  function choose(index: number, productId: number) {
    const next = rows.map((row, i) => (i === index ? { ...row, product_id: productId } : row))
    setRows(next)
    runCheck(next)
  }

  if (!allowed) return <Alert tone="warning">{t('assistant.documents.planNeeded')}</Alert>
  const matched = new Map<number, MatchedRow>(result?.rows.map((r) => [r.index, r]))
  const supplierId = result?.header.supplier_id as number | null | undefined
  const supplierCandidates = (result?.header.supplier_candidates as { supplier_id: number; name: string }[] | undefined) ?? []

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">{t('assistant.documents.heading')}</h2>
        <p className="text-sm text-slate-600">{t('assistant.documents.hint')}</p>
        <p className="text-xs text-slate-500">
          {t('assistant.documents.notKept')} {t('assistant.documents.untrusted')}
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm font-medium text-slate-800">
          {t('assistant.documents.kind')}
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as DocumentKind)
              setResult(null)
              setAction(null)
            }}
            className="ml-2 min-h-12 rounded-lg border border-slate-300 bg-white px-3"
          >
            <option value="invoice">{t('assistant.documents.kinds.invoice')}</option>
            <option value="stock_list">{t('assistant.documents.kinds.stock_list')}</option>
          </select>
        </label>
        <input ref={camera} type="file" accept={ACCEPT_ATTRIBUTE} capture="environment" className="sr-only" tabIndex={-1} onChange={(e) => void onFile(e)} />
        <input ref={pick} type="file" accept={ACCEPT_ATTRIBUTE} className="sr-only" tabIndex={-1} onChange={(e) => void onFile(e)} />
        <Button variant="secondary" onClick={() => camera.current?.click()}>
          <Camera aria-hidden="true" className="size-5" />
          {t('assistant.documents.take')}
        </Button>
        <Button variant="secondary" onClick={() => pick.current?.click()}>
          <ImagePlus aria-hidden="true" className="size-5" />
          {t('assistant.documents.upload')}
        </Button>
        {image && (
          <Button loading={read.isPending} disabled={!configured} onClick={() => read.mutate()}>
            {read.isPending ? t('assistant.documents.reading') : t('assistant.documents.read')}
          </Button>
        )}
      </div>
      {fileProblem && <p role="alert" className="text-sm font-medium text-red-700">{fileProblem}</p>}
      {(!configured || notConfigured) && (
        <Alert tone="info">
          <p className="font-semibold">{notConfigured ?? t('assistant.documents.notConfigured')}</p>
          <p className="text-sm">{t('assistant.documents.manualHint')}</p>
        </Alert>
      )}
      {read.isError && <ErrorNotice error={read.error} context="ai" safeToRepeat retry={() => read.mutate()} />}
      {warnings.map((warning) => (
        <Alert key={warning} tone="warning">
          {warning}
        </Alert>
      ))}

      {kind === 'invoice' && (
        <section className="grid grid-cols-1 gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:grid-cols-2">
          <TextField label={t('assistant.documents.supplier')} value={header.supplier} onChange={(e) => { setHeader({ ...header, supplier: e.target.value }); setResult(null) }} maxLength={120} />
          <TextField label={t('assistant.documents.invoiceNo')} value={header.invoice_no} onChange={(e) => { setHeader({ ...header, invoice_no: e.target.value }); setResult(null) }} maxLength={50} optional />
          <TextField label={t('assistant.documents.invoiceDate')} value={header.invoice_date} onChange={(e) => { setHeader({ ...header, invoice_date: e.target.value }); setResult(null) }} optional />
          <TextField label={t('assistant.documents.total')} value={header.total} inputMode="decimal" onChange={(e) => { setHeader({ ...header, total: e.target.value }); setResult(null) }} optional />
          {result && kind === 'invoice' && (
            <div className="sm:col-span-2 text-sm">
              {supplierId ? (
                <p className="text-emerald-800">{t('assistant.documents.supplierMatched', { name: header.supplier })}</p>
              ) : supplierCandidates.length > 0 ? (
                <p>
                  {t('assistant.documents.supplierPossible')}{' '}
                  {supplierCandidates.map((c) => (
                    <button key={c.supplier_id} type="button" className="mr-2 font-medium underline" onClick={() => { const next = { ...header, supplier: c.name }; setHeader(next); runCheck(rows, next) }}>
                      {c.name}
                    </button>
                  ))}
                </p>
              ) : (
                <p className="text-amber-800">{t('assistant.documents.supplierMissing')}</p>
              )}
              {result.header.invoice_duplicate === true && <p className="text-red-700">{t('assistant.documents.duplicateInvoice')}</p>}
            </div>
          )}
        </section>
      )}

      <section className="space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t('assistant.documents.lines')}</h3>
        <ul className="space-y-3">
          {rows.map((row, index) => {
            const m = matched.get(index)
            return (
              <li key={index} className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <div className="col-span-2">
                    <TextField label={t('assistant.documents.name')} value={row.name ?? ''} onChange={(e) => edit(index, { name: e.target.value, product_id: null })} maxLength={200} />
                  </div>
                  <TextField label={kind === 'invoice' ? t('assistant.documents.quantity') : t('assistant.documents.counted')} value={row.quantity ?? ''} inputMode="decimal" onChange={(e) => edit(index, { quantity: e.target.value })} />
                  {kind === 'invoice' && <TextField label={t('assistant.documents.price')} value={row.unit_price ?? ''} inputMode="decimal" onChange={(e) => edit(index, { unit_price: e.target.value })} />}
                  {kind === 'invoice' && <TextField label={t('assistant.documents.discount')} value={row.discount ?? ''} inputMode="decimal" onChange={(e) => edit(index, { discount: e.target.value })} optional />}
                  <TextField label={t('assistant.documents.barcode')} value={row.barcode ?? ''} inputMode="numeric" onChange={(e) => edit(index, { barcode: e.target.value, product_id: null })} optional />
                </div>
                {m && (
                  <div className="space-y-1 text-sm">
                    <Badge tone={TONE[m.status]}>{t(`assistant.documents.status.${m.status}`)}</Badge>
                    {m.status === 'MATCHED' && <span className="ml-2 text-slate-700">{m.candidates.find((c) => c.product_id === m.product_id)?.name ?? ''}</span>}
                    {m.status !== 'MATCHED' && m.candidates.length === 0 && <p className="text-slate-600">{t('assistant.documents.noMatch')}</p>}
                    {m.status === 'POSSIBLE_MATCH' && (
                      <ul className="space-y-1">
                        {m.candidates.map((c) => (
                          <li key={c.product_id} className="flex flex-wrap items-center gap-2">
                            <span>{c.name} <span className="font-mono text-xs text-slate-500">({c.sku})</span> — {c.reasons.join(', ')}</span>
                            <Button variant="secondary" onClick={() => choose(index, c.product_id)}>{t('assistant.documents.useThis')}</Button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {kind === 'stock_list' && m.system_quantity !== null && (
                      <p className="text-slate-700">
                        {t('assistant.documents.systemStock')}: {m.system_quantity} · {t('assistant.documents.difference')}: {m.difference}
                      </p>
                    )}
                    {m.problems.map((p) => <p key={p} role="alert" className="font-medium text-red-700">{p}</p>)}
                    {m.warnings.map((w) => <p key={w} className="text-amber-800">{w}</p>)}
                  </div>
                )}
                <Button variant="secondary" onClick={() => { setRows(rows.length > 1 ? rows.filter((_, i) => i !== index) : [{ ...EMPTY_ROW }]); setResult(null) }}>
                  {t('assistant.documents.removeLine')}
                </Button>
              </li>
            )
          })}
        </ul>
        <div className="flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => { setRows([...rows, { ...EMPTY_ROW }]); setResult(null) }}>{t('assistant.documents.addLine')}</Button>
          <Button loading={check.isPending} onClick={() => runCheck()}>
            {check.isPending ? t('assistant.documents.checking') : t('assistant.documents.check')}
          </Button>
        </div>
      </section>

      {check.isError && (check.error instanceof ApiError && check.error.category === 'validation'
        ? <Alert tone="error">{check.error.message}</Alert>
        : <ErrorNotice error={check.error} context="save" safeToRepeat retry={() => runCheck()} />)}

      {result && (
        <section className="space-y-2">
          <p className="text-sm text-slate-700">
            {t('assistant.documents.counts', { matched: result.counts.MATCHED ?? 0, possible: result.counts.POSSIBLE_MATCH ?? 0, fresh: result.counts.NEW_PRODUCT_CANDIDATE ?? 0 })}
          </p>
          {result.problems.map((p) => <Alert key={p} tone="warning">{p}</Alert>)}
          {!result.can_propose && <p className="text-sm text-slate-600">{t('assistant.documents.resolve')}</p>}
          {result.can_propose && !action && (
            <div>
              <p className="mb-2 text-sm text-slate-600">{kind === 'invoice' ? t('assistant.documents.fromInvoice') : t('assistant.documents.fromStock')}</p>
              <Button loading={prepare.isPending} onClick={() => prepare.mutate(result)}>
                {prepare.isPending ? t('assistant.documents.preparing') : t('assistant.documents.prepare')}
              </Button>
            </div>
          )}
          {prepare.isError && <ErrorNotice error={prepare.error} context="save" safeToRepeat retry={() => prepare.reset()} />}
        </section>
      )}
      {action && <ActionPreviewCard action={action} onChange={setAction} onClose={() => setAction(null)} />}
    </div>
  )
}
