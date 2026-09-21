import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { describeError, type ErrorContext, type RecoveryAction } from '@/lib/errors'

import { Alert, Button } from './ui'

export type ErrorHandlers = Partial<Record<RecoveryAction, () => void>>

interface Props extends ErrorHandlers {
  error: unknown
  context?: ErrorContext
  /** Is repeating the same request safe? True for reads and for writes that carry an idempotency key. */
  safeToRepeat?: boolean
  /** Extra facts for the person, for example the stock that is left. Never a technical detail. */
  children?: React.ReactNode
  /** A more specific label for an action, for example "Continue without comparison". */
  labels?: Partial<Record<RecoveryAction, string>>
}

const ACTION_KEYS: Record<RecoveryAction, string> = {
  retry: 'recovery.actions.retry',
  save_draft: 'recovery.actions.save_draft',
  go_back: 'recovery.actions.go_back',
  refresh: 'recovery.actions.refresh',
  continue: 'recovery.actions.continue',
  manual_entry: 'recovery.actions.manual_entry',
  choose_another: 'recovery.actions.choose_another',
  adjust_cart: 'recovery.actions.adjust_cart',
  cancel: 'recovery.actions.cancel',
  contact_support: 'recovery.actions.contact_support',
}

/**
 * The one way a failure is shown. It never prints the error's own text unless the server marked it as written for
 * people (a business rule or a validation), it shows the reference to quote, and it offers only the actions that
 * exist on the screen AND make sense (see `describeError`). "Refresh" always works, so a person is never stuck.
 */
export function ErrorNotice({ error, context = 'generic', safeToRepeat, children, labels, ...handlers }: Props) {
  const { t } = useTranslation()
  const [supportShown, setSupportShown] = useState(false)
  const recovery = describeError(error, { context, safeToRepeat })
  // "Contact support" has no address to invent: it copies the reference and tells the person to quote it.
  const contactSupport = () => {
    setSupportShown(true)
    try {
      void navigator.clipboard?.writeText(recovery.reference ?? '')
    } catch {
      // The reference is on screen anyway.
    }
  }
  const wanted = recovery.actions.filter(
    (action) => action === 'refresh' || action === 'contact_support' || handlers[action] !== undefined,
  )
  // A person is never left without a next step, unless the fix is simply to change what they typed.
  const actions: RecoveryAction[] = wanted.length > 0 || recovery.needsInputChange ? wanted : ['refresh']

  return (
    <Alert tone="error">
      <p className="font-semibold">{t(recovery.titleKey)}</p>
      {recovery.serverMessage && <p className="mt-1">{recovery.serverMessage}</p>}
      {recovery.messageKey && <p className="mt-1">{t(recovery.messageKey)}</p>}
      {children}
      {recovery.preserved && <p className="mt-1 text-sm">{t('recovery.preserved')}</p>}
      {recovery.waitSeconds !== null && recovery.category === 'rate_limited' && (
        <p className="mt-1">{t('recovery.wait', { count: recovery.waitSeconds })}</p>
      )}
      {recovery.reference && <p className="mt-1 text-sm">{t('recovery.reference', { id: recovery.reference })}</p>}
      {supportShown && <p className="mt-1 text-sm">{t('recovery.supportHint', { id: recovery.reference ?? '' })}</p>}
      {actions.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {actions.map((action, index) => (
            <Button
              key={action}
              variant={index === 0 ? 'primary' : 'secondary'}
              onClick={handlers[action] ?? (action === 'contact_support' ? contactSupport : () => window.location.reload())}
            >
              {labels?.[action] ?? t(ACTION_KEYS[action] as never)}
            </Button>
          ))}
        </div>
      )}
    </Alert>
  )
}
