import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { createCustomer, getCustomer, updateCustomer } from '@/api/customers'
import type { Customer } from '@/api/types'
import { buttonClasses } from '@/components/buttonStyles'
import { TextAreaField, TextField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { RestoreBanner } from '@/components/RestoreBanner'
import { useFailure, needsNotice } from '@/hooks/useFailure'
import { useFormBackup } from '@/hooks/useFormBackup'
import { useIdempotencyKey } from '@/lib/idempotency'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'
import { CURRENCY_SYMBOL } from '@/lib/format'

import {
  API_FIELD_TO_FORM_FIELD,
  buildPayload,
  emptyValues,
  validate,
  valuesFromCustomer,
  type FieldErrors,
  type FieldName,
  type FormValues,
} from './customerForm'

export function CustomerFormPage({ mode }: { mode: 'create' | 'edit' }) {
  const { t } = useTranslation()
  const customerId = Number(useParams().id)
  const validId = mode === 'create' || (Number.isInteger(customerId) && customerId > 0)
  const customer = useQuery({
    queryKey: ['customer', customerId],
    queryFn: () => getCustomer(customerId),
    enabled: mode === 'edit' && validId,
  })

  const title = mode === 'create' ? t('customers.form.addTitle') : t('customers.form.editTitle')

  if (!validId || customer.isError) {
    const notFound = !validId || (customer.error instanceof ApiError && customer.error.status === 404)
    return (
      <div className="space-y-6">
        <PageHeader title={title} />
        {notFound ? <Alert tone="error">{t('customers.detail.notFound')}</Alert> : <QueryError error={customer.error} onRetry={() => void customer.refetch()} />}
        <LinkButton to="/customers" variant="secondary">
          {t('customers.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (mode === 'edit' && !customer.data) return <Spinner />

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title={title} />
      <CustomerForm mode={mode} customer={customer.data} />
    </div>
  )
}

function CustomerForm({ mode, customer }: { mode: 'create' | 'edit'; customer: Customer | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [values, setValues] = useState<FormValues>(() => (customer ? valuesFromCustomer(customer) : emptyValues()))
  const [clientErrors, setClientErrors] = useState<FieldErrors>({})
  const [serverErrors, setServerErrors] = useState<Partial<Record<FieldName, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const { failure, setFailure } = useFailure()
  const idem = useIdempotencyKey()
  const backup = useFormBackup('customer.new', values, { enabled: mode === 'create' })

  function setField(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
    setClientErrors((current) => ({ ...current, [field]: undefined }))
    setServerErrors((current) => ({ ...current, [field]: undefined }))
  }

  function errorFor(field: FieldName): string | undefined {
    const code = clientErrors[field]
    if (code) return t(`customers.form.validation.${code}`)
    return serverErrors[field]
  }

  const save = useMutation({
    mutationFn: () => {
      const payload = buildPayload(values)
      if (mode !== 'create') return updateCustomer(customer!.id, payload)
      const body = { ...payload, opening_balance: values.openingBalance.trim() || null }
      return createCustomer(body, { idempotencyKey: idem.keyFor(JSON.stringify(body)) })
    },
    onSuccess: async (result) => {
      idem.renew()
      backup.clear()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['customers'] }),
        queryClient.invalidateQueries({ queryKey: ['customer', result.customer.id] }),
        queryClient.invalidateQueries({ queryKey: ['customerLedger', result.customer.id] }),
      ])
      void navigate(`/customers/${result.customer.id}`, { state: { warnings: result.warnings } })
    },
    onError: (error) => {
      if (needsNotice(error)) {
        setFailure(error)
        return
      }
      if (error instanceof ApiError) {
        const mapped: Partial<Record<FieldName, string>> = {}
        for (const [apiField, message] of Object.entries(error.fieldErrors)) {
          const field = API_FIELD_TO_FORM_FIELD[apiField]
          if (field) mapped[field] = message
        }
        setServerErrors(mapped)
        setFormError(Object.keys(mapped).length === 0 ? error.message : t('customers.form.fixErrors'))
      } else {
        setFormError(t('common.genericError'))
      }
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setFormError(null)
    setFailure(null)
    setServerErrors({})
    const errors = validate(values, mode === 'create')
    setClientErrors(errors)
    if (Object.keys(errors).length > 0) {
      setFormError(t('customers.form.fixErrors'))
      return
    }
    save.mutate()
  }

  return (
    <form onSubmit={submit} noValidate className="space-y-6">
      {backup.restorable && (
        <RestoreBanner
          onRestore={() => {
            setValues(backup.restorable!)
            backup.dismiss()
          }}
          onDiscard={backup.clear}
        />
      )}
      {formError && <Alert tone="error">{formError}</Alert>}
        {failure !== null && (
          <ErrorNotice error={failure} context="save" safeToRepeat={true} retry={() => save.mutate()} cancel={() => void navigate('/customers')} />
        )}

      <Section title={t('customers.form.contactSection')}>
        <div className="sm:col-span-2">
          <TextField
            label={t('customers.form.fields.name')}
            value={values.name}
            onChange={(event) => setField('name', event.target.value)}
            error={errorFor('name')}
            maxLength={200}
            autoFocus
          />
        </div>
        <TextField
          label={t('customers.form.fields.phone')}
          hint={t('customers.form.hints.phone')}
          optional
          value={values.phone}
          onChange={(event) => setField('phone', event.target.value)}
          error={errorFor('phone')}
          inputMode="tel"
          maxLength={40}
        />
        <TextField
          label={t('customers.form.fields.email')}
          optional
          value={values.email}
          onChange={(event) => setField('email', event.target.value)}
          error={errorFor('email')}
          type="email"
          inputMode="email"
          maxLength={254}
        />
        <div className="sm:col-span-2">
          <TextAreaField
            label={t('customers.form.fields.address')}
            optional
            value={values.address}
            onChange={(event) => setField('address', event.target.value)}
            error={errorFor('address')}
            rows={2}
            maxLength={500}
          />
        </div>
        <div className="sm:col-span-2">
          <TextAreaField
            label={t('customers.form.fields.notes')}
            optional
            value={values.notes}
            onChange={(event) => setField('notes', event.target.value)}
            error={errorFor('notes')}
            maxLength={2000}
          />
        </div>
      </Section>

      {mode === 'create' && (
        <Section title={t('customers.form.openingSection')}>
          <div className="sm:col-span-2">
            <TextField
              label={t('customers.form.fields.openingBalance')}
              hint={t('customers.form.hints.openingBalance')}
              optional
              value={values.openingBalance}
              onChange={(event) => setField('openingBalance', event.target.value)}
              error={errorFor('openingBalance')}
              prefix={CURRENCY_SYMBOL}
              inputMode="decimal"
            />
          </div>
        </Section>
      )}

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Link to={customer ? `/customers/${customer.id}` : '/customers'} className={buttonClasses('secondary')}>
          {t('common.cancel')}
        </Link>
        <Button type="submit" loading={save.isPending}>
          {save.isPending ? t('common.saving') : mode === 'create' ? t('customers.form.submitAdd') : t('customers.form.submitEdit')}
        </Button>
      </div>
    </form>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">{children}</div>
    </section>
  )
}
