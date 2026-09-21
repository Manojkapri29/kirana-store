import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { cancelAction, confirmAction, editAction, refreshActionStock, type AiAction } from '@/api/ai'
import { TextField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { Alert, Button } from '@/components/ui'

type Payload = Record<string, unknown>
type Item = Record<string, unknown>

const RESULT_LINK: Record<string, (ids: number[]) => string> = {
  purchase: (ids) => `/purchases/${ids[0]}`,
  inventory_adjustment: () => '/inventory',
  promotion: (ids) => `/promotions/${ids[0]}`,
}

interface Props {
  action: AiAction
  onChange: (action: AiAction) => void
  /** Called after the person closes a finished (confirmed or cancelled) action. */
  onClose?: () => void
}

/**
 * Exactly what would happen, shown BEFORE anything happens: the action, the shop, the records, the quantities, the
 * amounts and the expected effect. Confirm runs an existing service on the server; Edit changes what would be done;
 * Cancel changes nothing. The card says "done" only after the server has answered that it really happened.
 */
export function ActionPreviewCard({ action, onChange, onClose }: Props) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Payload>(action.current)
  const [failure, setFailure] = useState<unknown>(null)
  const preview = action.preview
  const open = action.status === 'PROPOSED' || action.status === 'FAILED'

  const refreshData = () =>
    Promise.all(
      ['purchases', 'purchase', 'inventory', 'products', 'product', 'history', 'promotions', 'aiStatus'].map((key) =>
        queryClient.invalidateQueries({ queryKey: [key] }),
      ),
    )

  const run = useMutation({
    mutationFn: (kind: 'confirm' | 'cancel' | 'edit' | 'refresh') => {
      if (kind === 'confirm') return confirmAction(action.id)
      if (kind === 'cancel') return cancelAction(action.id)
      if (kind === 'refresh') return refreshActionStock(action.id)
      return editAction(action.id, draft)
    },
    onSuccess: async (result, kind) => {
      onChange(result)
      setFailure(null)
      if (kind === 'edit' || kind === 'refresh') setEditing(false)
      if (kind === 'confirm') await refreshData()
    },
    onError: (error) => setFailure(error),
  })

  const items = (draft.items as Item[] | undefined) ?? []
  const setItem = (index: number, changes: Item) =>
    setDraft((current) => ({ ...current, items: ((current.items as Item[]) ?? []).map((item, i) => (i === index ? { ...item, ...changes } : item)) }))
  const names = preview.lines?.rows.map((row) => row[0]) ?? []
  const label = t(`assistant.action.kinds.${action.kind}`)

  return (
    <section className="space-y-4 rounded-xl border border-emerald-300 bg-white p-4 shadow-sm">
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800">{t('assistant.action.heading')}</p>
        <h3 className="text-lg font-bold text-slate-900">{preview.title === action.kind ? label : preview.title}</h3>
        <p className="text-sm text-slate-600">
          {t('assistant.action.shop')}: {preview.shop}
          {preview.summary ? ` · ${preview.summary}` : ''}
        </p>
      </div>

      {action.status === 'EXECUTED' && action.result_type && action.result_ids && (
        <Alert tone="success">
          <p>{t(`assistant.action.done.${action.result_type as 'purchase'}`)}</p>
          <Link to={RESULT_LINK[action.result_type]?.(action.result_ids) ?? '/'} className="mt-1 inline-block font-medium underline">
            {t(`assistant.action.open.${action.result_type as 'purchase'}`)}
          </Link>
        </Alert>
      )}
      {action.status === 'CANCELLED' && <Alert tone="info">{t('assistant.action.cancelled')}</Alert>}

      {open && preview.lines && !editing && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr>
                {preview.lines.columns.map((column) => (
                  <th key={column} className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {preview.lines.rows.map((row, index) => (
                <tr key={index}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="px-3 py-2">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open && !editing && preview.totals.map((total) => (
        <p key={total.label} className="flex justify-between text-slate-800">
          <span>{total.label}</span>
          <span className="text-lg font-bold">{total.value}</span>
        </p>
      ))}

      {open && !editing && preview.impact.map((line) => (
        <p key={line} className="text-sm text-slate-700">
          {line}
        </p>
      ))}

      {open && preview.problems.length > 0 && (
        <Alert tone="warning">
          <p className="font-semibold">{t('assistant.action.problems')}</p>
          <ul className="list-disc pl-5">
            {preview.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
          {action.kind === 'STOCK_ADJUSTMENT' && preview.problems.some((p) => p.includes('changed since')) && (
            <Button variant="secondary" className="mt-2" onClick={() => run.mutate('refresh')}>
              {t('assistant.action.updateStock')}
            </Button>
          )}
        </Alert>
      )}
      {open && preview.warnings.length > 0 && (
        <p className="text-sm text-slate-600">
          {t('assistant.action.warnings')}: {preview.warnings.join(' ')}
        </p>
      )}

      {open && editing && (
        <div className="space-y-3">
          {action.kind === 'PROMOTION_DRAFT' ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <TextField label={t('assistant.action.fields.name')} value={String(draft.name ?? '')} onChange={(e) => setDraft({ ...draft, name: e.target.value })} maxLength={120} />
              {draft.promo_type === 'PERCENT' ? (
                <TextField label={t('assistant.action.fields.percent')} value={String(draft.percent ?? '')} inputMode="decimal" onChange={(e) => setDraft({ ...draft, percent: e.target.value })} />
              ) : (
                <TextField label={t('assistant.action.fields.amount')} value={String(draft.amount ?? '')} inputMode="decimal" onChange={(e) => setDraft({ ...draft, amount: e.target.value })} />
              )}
              {draft.min_cart_value != null && (
                <TextField label={t('assistant.action.fields.minCart')} value={String(draft.min_cart_value)} inputMode="decimal" onChange={(e) => setDraft({ ...draft, min_cart_value: e.target.value })} />
              )}
            </div>
          ) : (
            <ul className="space-y-3">
              {items.map((item, index) => (
                <li key={index} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <p className="mb-2 font-medium text-slate-900">{names[index] ?? `#${index + 1}`}</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {action.kind === 'PURCHASE_DRAFT' ? (
                      <>
                        <TextField label={t('assistant.action.fields.quantity')} value={String(item.quantity ?? '')} inputMode="decimal" onChange={(e) => setItem(index, { quantity: e.target.value })} />
                        <TextField label={t('assistant.action.fields.unitCost')} value={String(item.unit_cost ?? '')} inputMode="decimal" onChange={(e) => setItem(index, { unit_cost: e.target.value === '' ? null : e.target.value })} />
                      </>
                    ) : (
                      <TextField label={t('assistant.action.fields.counted')} value={String(item.counted_quantity ?? '')} inputMode="decimal" onChange={(e) => setItem(index, { counted_quantity: e.target.value })} />
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {failure !== null && <ErrorNotice error={failure} context="save" safeToRepeat={false} retry={() => run.mutate('confirm')} />}

      <div className="flex flex-wrap gap-3">
        {open && !editing && (
          <>
            <Button loading={run.isPending && run.variables === 'confirm'} disabled={run.isPending || !preview.can_confirm} onClick={() => run.mutate('confirm')}>
              {run.isPending && run.variables === 'confirm' ? t('assistant.action.confirming') : t('assistant.action.confirm')}
            </Button>
            <Button variant="secondary" disabled={run.isPending} onClick={() => { setDraft(action.current); setEditing(true) }}>
              {t('assistant.action.edit')}
            </Button>
            <Button variant="secondary" disabled={run.isPending} onClick={() => run.mutate('cancel')}>
              {t('assistant.action.cancel')}
            </Button>
          </>
        )}
        {open && editing && (
          <>
            <Button loading={run.isPending && run.variables === 'edit'} onClick={() => run.mutate('edit')}>
              {t('assistant.action.applyEdit')}
            </Button>
            <Button variant="secondary" onClick={() => setEditing(false)}>
              {t('assistant.action.cancel')}
            </Button>
          </>
        )}
        {!open && onClose && (
          <Button variant="secondary" onClick={onClose}>
            {t('assistant.action.close')}
          </Button>
        )}
      </div>
    </section>
  )
}
