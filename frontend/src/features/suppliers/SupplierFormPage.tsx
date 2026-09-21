import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { createSupplier, getSupplier, updateSupplier } from '@/api/suppliers'
import type { Supplier } from '@/api/types'
import { buttonClasses } from '@/components/buttonStyles'
import { TextAreaField, TextField } from '@/components/fields'
import { ErrorNotice } from '@/components/ErrorNotice'
import { RestoreBanner } from '@/components/RestoreBanner'
import { useFailure, needsNotice } from '@/hooks/useFailure'
import { useFormBackup } from '@/hooks/useFormBackup'
import { Alert, Button, LinkButton, PageHeader, QueryError, Spinner } from '@/components/ui'

import {
  API_FIELD_TO_FORM_FIELD,
  buildPayload,
  emptyValues,
  validate,
  valuesFromSupplier,
  type FieldErrors,
  type FieldName,
  type FormValues,
} from './supplierForm'

export function SupplierFormPage({ mode }: { mode: 'create' | 'edit' }) {
  const { t } = useTranslation()
  const supplierId = Number(useParams().id)
  const validId = mode === 'create' || (Number.isInteger(supplierId) && supplierId > 0)
  const supplier = useQuery({
    queryKey: ['supplier', supplierId],
    queryFn: () => getSupplier(supplierId),
    enabled: mode === 'edit' && validId,
  })

  const title = mode === 'create' ? t('suppliers.form.addTitle') : t('suppliers.form.editTitle')

  if (!validId || supplier.isError) {
    const notFound = !validId || (supplier.error instanceof ApiError && supplier.error.status === 404)
    return (
      <div className="space-y-6">
        <PageHeader title={title} />
        {notFound ? (
          <Alert tone="error">{t('suppliers.detail.notFound')}</Alert>
        ) : (
          <QueryError error={supplier.error} onRetry={() => void supplier.refetch()} />
        )}
        <LinkButton to="/suppliers" variant="secondary">
          {t('suppliers.detail.backToList')}
        </LinkButton>
      </div>
    )
  }
  if (mode === 'edit' && !supplier.data) return <Spinner />

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title={title} />
      <SupplierForm mode={mode} supplier={supplier.data} />
    </div>
  )
}

function SupplierForm({ mode, supplier }: { mode: 'create' | 'edit'; supplier: Supplier | undefined }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [values, setValues] = useState<FormValues>(() => (supplier ? valuesFromSupplier(supplier) : emptyValues()))
  const [clientErrors, setClientErrors] = useState<FieldErrors>({})
  const [serverErrors, setServerErrors] = useState<Partial<Record<FieldName, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const { failure, setFailure } = useFailure()
  const backup = useFormBackup('supplier.new', values, { enabled: mode === 'create' })

  function setField(field: FieldName, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
    setClientErrors((current) => ({ ...current, [field]: undefined }))
    setServerErrors((current) => ({ ...current, [field]: undefined }))
  }

  function errorFor(field: FieldName): string | undefined {
    const code = clientErrors[field]
    if (code) return t(`suppliers.form.validation.${code}`)
    return serverErrors[field]
  }

  const save = useMutation({
    mutationFn: () =>
      mode === 'create' ? createSupplier(buildPayload(values)) : updateSupplier(supplier!.id, buildPayload(values)),
    onSuccess: async (result) => {
      backup.clear()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['suppliers'] }),
        queryClient.invalidateQueries({ queryKey: ['supplierOptions'] }),
        queryClient.invalidateQueries({ queryKey: ['supplier', result.supplier.id] }),
        queryClient.invalidateQueries({ queryKey: ['products'] }), // product screens show the supplier's name
      ])
      void navigate(`/suppliers/${result.supplier.id}`, { state: { warnings: result.warnings } })
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
        setFormError(Object.keys(mapped).length === 0 ? error.message : t('suppliers.form.fixErrors'))
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
    const errors = validate(values)
    setClientErrors(errors)
    if (Object.keys(errors).length > 0) {
      setFormError(t('suppliers.form.fixErrors'))
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
          <ErrorNotice error={failure} context="save" safeToRepeat={mode === 'edit'} retry={() => save.mutate()} cancel={() => void navigate('/suppliers')} />
        )}

      <Section title={t('suppliers.form.contactSection')}>
        <div className="sm:col-span-2">
          <TextField
            label={t('suppliers.form.fields.name')}
            value={values.name}
            onChange={(event) => setField('name', event.target.value)}
            error={errorFor('name')}
            maxLength={200}
            autoFocus
          />
        </div>
        <TextField
          label={t('suppliers.form.fields.phone')}
          hint={t('suppliers.form.hints.phone')}
          optional
          value={values.phone}
          onChange={(event) => setField('phone', event.target.value)}
          error={errorFor('phone')}
          inputMode="tel"
          maxLength={40}
        />
        <TextField
          label={t('suppliers.form.fields.alternatePhone')}
          optional
          value={values.alternatePhone}
          onChange={(event) => setField('alternatePhone', event.target.value)}
          error={errorFor('alternatePhone')}
          inputMode="tel"
          maxLength={40}
        />
        <div className="sm:col-span-2">
          <TextField
            label={t('suppliers.form.fields.email')}
            optional
            value={values.email}
            onChange={(event) => setField('email', event.target.value)}
            error={errorFor('email')}
            type="email"
            inputMode="email"
            maxLength={254}
          />
        </div>
        <div className="sm:col-span-2">
          <TextAreaField
            label={t('suppliers.form.fields.address')}
            optional
            value={values.address}
            onChange={(event) => setField('address', event.target.value)}
            error={errorFor('address')}
            rows={2}
            maxLength={500}
          />
        </div>
      </Section>

      <Section title={t('suppliers.form.detailsSection')}>
        <TextField
          label={t('suppliers.form.fields.gstin')}
          hint={t('suppliers.form.hints.gstin')}
          optional
          value={values.gstin}
          onChange={(event) => setField('gstin', event.target.value)}
          error={errorFor('gstin')}
          autoCapitalize="characters"
          maxLength={30}
        />
        <div className="sm:col-span-2">
          <TextAreaField
            label={t('suppliers.form.fields.notes')}
            hint={t('suppliers.form.hints.notes')}
            optional
            value={values.notes}
            onChange={(event) => setField('notes', event.target.value)}
            error={errorFor('notes')}
            maxLength={2000}
          />
        </div>
      </Section>

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        <Link to={supplier ? `/suppliers/${supplier.id}` : '/suppliers'} className={buttonClasses('secondary')}>
          {t('common.cancel')}
        </Link>
        <Button type="submit" loading={save.isPending}>
          {save.isPending ? t('common.saving') : mode === 'create' ? t('suppliers.form.submitAdd') : t('suppliers.form.submitEdit')}
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
