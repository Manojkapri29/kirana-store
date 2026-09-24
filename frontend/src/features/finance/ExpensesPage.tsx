import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  createCategory, createExpense, expenseAction, listCategories, listExpenses, PAYMENT_METHODS, rejectExpense, setCategoryActive,
  voidExpense, type Expense, type ExpenseStatus, type PaymentMethod,
} from '@/api/finance'
import { FilterSelect, SelectField, TextField } from '@/components/fields'
import { Alert, Badge, Button, EmptyState, PageHeader, QueryError, Spinner, type BadgeTone } from '@/components/ui'
import { formatDate, formatMoney } from '@/lib/format'

import { FinanceTabs } from './common'
import { isoDate, useErrorText } from './financeUtils'

const STATUSES: readonly ExpenseStatus[] = ['DRAFT', 'SUBMITTED', 'APPROVED', 'POSTED', 'REJECTED', 'VOIDED']
const TONE: Record<ExpenseStatus, BadgeTone> = { DRAFT: 'slate', SUBMITTED: 'amber', APPROVED: 'amber', POSTED: 'green', REJECTED: 'red', VOIDED: 'red' }

function NewExpense({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const categories = useQuery({ queryKey: ['expenseCategories'], queryFn: listCategories })
  const [date, setDate] = useState(isoDate(new Date()))
  const [category, setCategory] = useState('')
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState<PaymentMethod>('CASH')
  const [payee, setPayee] = useState('')
  const [description, setDescription] = useState('')
  const create = useMutation({
    mutationFn: () => createExpense({ expense_date: date, category_id: Number(category), amount, payment_method: method, payee: payee || null, description: description || null }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ['expenses'] }); onDone() },
  })
  const active = (categories.data ?? []).filter((c) => c.is_active)
  return (
    <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(e) => { e.preventDefault(); create.mutate() }}>
      <div className="grid gap-4 md:grid-cols-3">
        <TextField label={t('finance.expense.date')} type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
        <SelectField label={t('finance.expense.category')} value={category} onChange={(e) => setCategory(e.target.value)} required>
          <option value="">{t('finance.expense.chooseCategory')}</option>
          {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </SelectField>
        <TextField label={t('finance.amount')} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        <SelectField label={t('finance.method')} value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
          {PAYMENT_METHODS.map((m) => <option key={m} value={m}>{t(`finance.methods.${m}`)}</option>)}
        </SelectField>
        <TextField label={t('finance.expense.payee')} value={payee} onChange={(e) => setPayee(e.target.value)} optional />
        <TextField label={t('finance.expense.description')} value={description} onChange={(e) => setDescription(e.target.value)} optional />
      </div>
      {active.length === 0 && <Alert tone="info">{t('finance.expense.needCategory')}</Alert>}
      <Alert tone="info">{t('finance.expense.draftNote')}</Alert>
      {create.isError && <Alert tone="error">{errorText(create.error)}</Alert>}
      <div className="flex gap-3">
        <Button type="submit" loading={create.isPending} disabled={!category || !amount}>{t('finance.expense.saveDraft')}</Button>
        <Button variant="secondary" onClick={onDone}>{t('common.cancel')}</Button>
      </div>
    </form>
  )
}

function Categories() {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const list = useQuery({ queryKey: ['expenseCategories'], queryFn: listCategories })
  const [name, setName] = useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['expenseCategories'] })
  const add = useMutation({ mutationFn: () => createCategory(name), onSuccess: async () => { setName(''); await refresh() } })
  const toggle = useMutation({ mutationFn: (c: { id: number; on: boolean }) => setCategoryActive(c.id, c.on), onSuccess: refresh })
  return (
    <section aria-labelledby="cats" className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 id="cats" className="text-lg font-semibold">{t('finance.expense.categories')}</h2>
      <p className="text-sm text-slate-600">{t('finance.expense.categoriesHint')}</p>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); add.mutate() }}>
        <TextField label={t('finance.expense.newCategory')} value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" requires="FINANCE_EXPENSE_MANAGE" loading={add.isPending} disabled={!name.trim()}>{t('finance.expense.addCategory')}</Button>
      </form>
      {(add.isError || toggle.isError) && <Alert tone="error">{errorText(add.error ?? toggle.error)}</Alert>}
      <ul className="flex flex-wrap gap-2">
        {list.data?.map((c) => (
          <li key={c.id} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1 text-sm">
            <span className={c.is_active ? '' : 'text-slate-400 line-through'}>{c.name}</span>
            <Button variant="secondary" requires="FINANCE_EXPENSE_MANAGE" onClick={() => toggle.mutate({ id: c.id, on: !c.is_active })}>{c.is_active ? t('finance.expense.deactivate') : t('finance.expense.activate')}</Button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Row({ e, names }: { e: Expense; names: Record<number, string> }) {
  const { t } = useTranslation()
  const errorText = useErrorText()
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('')
  const [asking, setAsking] = useState<'reject' | 'void' | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['expenses'] })
  const act = useMutation({ mutationFn: (a: 'submit' | 'approve' | 'post') => expenseAction(e.id, a), onSuccess: refresh })
  const withReason = useMutation({
    mutationFn: () => (asking === 'reject' ? rejectExpense(e.id, reason) : voidExpense(e.id, reason)),
    onSuccess: async () => { setAsking(null); setReason(''); await refresh() },
  })
  const err = act.error ?? withReason.error
  return (
    <li className="space-y-2 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold">{e.expense_no}</span>
        <Badge tone={TONE[e.status]}>{t(`finance.expense.status.${e.status}`)}</Badge>
        <span className="ml-auto font-semibold">{formatMoney(e.amount)}</span>
      </div>
      <p className="text-sm text-slate-600">{formatDate(e.expense_date)} · {names[e.category_id] ?? '—'} · {t(`finance.methods.${e.payment_method}`)}{e.payee ? ` · ${e.payee}` : ''}{e.description ? ` · ${e.description}` : ''}</p>
      {e.status === 'SUBMITTED' && <Alert tone="warning">{t('finance.expense.waiting')}</Alert>}
      {e.rejection_reason && <p className="text-sm text-red-700">{t('finance.expense.rejectedBecause', { reason: e.rejection_reason })}</p>}
      {e.void_reason && <p className="text-sm text-red-700">{t('finance.expense.voidedBecause', { reason: e.void_reason })}</p>}
      {err && <Alert tone="error">{errorText(err)}</Alert>}
      <div className="flex flex-wrap gap-2">
        {e.status === 'DRAFT' && <Button requires="FINANCE_EXPENSE_MANAGE" loading={act.isPending} onClick={() => act.mutate('submit')}>{t('finance.expense.submit')}</Button>}
        {e.status === 'SUBMITTED' && <Button requires="FINANCE_EXPENSE_APPROVE" loading={act.isPending} onClick={() => act.mutate('approve')}>{t('finance.expense.approve')}</Button>}
        {e.status === 'SUBMITTED' && <Button variant="secondary" requires="FINANCE_EXPENSE_APPROVE" onClick={() => setAsking('reject')}>{t('finance.expense.reject')}</Button>}
        {e.status === 'APPROVED' && <Button requires="FINANCE_EXPENSE_MANAGE" loading={act.isPending} onClick={() => act.mutate('post')}>{t('finance.expense.post')}</Button>}
        {e.status === 'POSTED' && <Button variant="secondary" requires="FINANCE_EXPENSE_MANAGE" onClick={() => setAsking('void')}>{t('finance.expense.void')}</Button>}
      </div>
      {asking && (
        <div className="flex flex-wrap items-end gap-3">
          <TextField label={t('finance.reason')} value={reason} onChange={(ev) => setReason(ev.target.value)} />
          <Button loading={withReason.isPending} disabled={!reason.trim()} onClick={() => withReason.mutate()}>{asking === 'reject' ? t('finance.expense.reject') : t('finance.expense.void')}</Button>
          <Button variant="secondary" onClick={() => setAsking(null)}>{t('common.cancel')}</Button>
        </div>
      )}
    </li>
  )
}

export function ExpensesPage() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<ExpenseStatus | ''>('')
  const [creating, setCreating] = useState(false)
  const categories = useQuery({ queryKey: ['expenseCategories'], queryFn: listCategories })
  const list = useQuery({ queryKey: ['expenses', status], queryFn: () => listExpenses({ status, limit: 100 }) })
  const names = Object.fromEntries((categories.data ?? []).map((c) => [c.id, c.name]))
  return (
    <div className="space-y-6">
      <PageHeader title={t('finance.expense.title')} subtitle={t('finance.expense.subtitle')} actions={<Button requires="FINANCE_EXPENSE_MANAGE" onClick={() => setCreating(true)}><Plus aria-hidden="true" className="size-5" />{t('finance.expense.add')}</Button>} />
      <FinanceTabs />
      {creating && <NewExpense onDone={() => setCreating(false)} />}
      <Categories />
      <FilterSelect aria-label={t('finance.expense.filter')} value={status} onChange={(e) => setStatus(e.target.value as ExpenseStatus | '')}>
        <option value="">{t('finance.expense.allStatuses')}</option>
        {STATUSES.map((s) => <option key={s} value={s}>{t(`finance.expense.status.${s}`)}</option>)}
      </FilterSelect>
      {list.isError && <QueryError error={list.error} onRetry={() => void list.refetch()} />}
      {list.isPending && <Spinner />}
      {list.data && list.data.items.length === 0 && <EmptyState title={t('finance.expense.empty')} hint={t('finance.expense.emptyHint')} />}
      <ul className="space-y-3">{list.data?.items.map((e) => <Row key={e.id} e={e} names={names} />)}</ul>
    </div>
  )
}
