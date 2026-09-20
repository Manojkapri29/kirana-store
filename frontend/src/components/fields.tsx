import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'
import { useTranslation } from 'react-i18next'

const CONTROL =
  'block min-h-12 w-full rounded-lg border bg-white px-3 text-base text-slate-900 placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-emerald-600 disabled:bg-slate-100 disabled:text-slate-500'

interface FieldChrome {
  label: string
  error?: string | null
  hint?: string
  optional?: boolean
}

function FieldFrame({
  id,
  label,
  error,
  hint,
  optional,
  children,
}: FieldChrome & { id: string; children: ReactNode }) {
  const { t } = useTranslation()
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-800">
        {label}
        {optional && <span className="ml-1 font-normal text-slate-500">({t('common.optional')})</span>}
      </label>
      {children}
      {hint && !error && (
        <p id={`${id}-hint`} className="mt-1.5 text-sm text-slate-500">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="mt-1.5 text-sm font-medium text-red-700">
          {error}
        </p>
      )}
    </div>
  )
}

interface TextFieldProps extends FieldChrome, Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  /** Text shown inside the field before the value, e.g. "₹". */
  prefix?: string
  /** Text shown inside the field after the value, e.g. a unit. */
  suffix?: string
}

export function TextField({ label, error, hint, optional, prefix, suffix, className, ...input }: TextFieldProps) {
  const id = useId()
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined
  return (
    <FieldFrame id={id} label={label} error={error} hint={hint} optional={optional}>
      <div className="relative">
        {prefix && (
          <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-slate-500">{prefix}</span>
        )}
        <input
          {...input}
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={`${CONTROL} ${error ? 'border-red-500' : 'border-slate-300'} ${prefix ? 'pl-8' : ''} ${suffix ? 'pr-14' : ''} ${className ?? ''}`}
        />
        {suffix && (
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-slate-500">{suffix}</span>
        )}
      </div>
    </FieldFrame>
  )
}

interface SelectFieldProps extends FieldChrome, Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {}

export function SelectField({ label, error, hint, optional, className, children, ...select }: SelectFieldProps) {
  const id = useId()
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined
  return (
    <FieldFrame id={id} label={label} error={error} hint={hint} optional={optional}>
      <select
        {...select}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`${CONTROL} ${error ? 'border-red-500' : 'border-slate-300'} ${className ?? ''}`}
      >
        {children}
      </select>
    </FieldFrame>
  )
}

/** A compact select for filter bars (no label; give it an aria-label). */
export function FilterSelect({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${CONTROL} border-slate-300 sm:w-auto ${className ?? ''}`} />
}
