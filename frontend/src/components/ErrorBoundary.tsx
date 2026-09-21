import { Component, type ErrorInfo, type ReactNode } from 'react'
import { withTranslation, type WithTranslation } from 'react-i18next'

import { buttonClasses } from './buttonStyles'
import { Alert, Button } from './ui'

interface Props extends WithTranslation {
  children: ReactNode
}

interface State {
  failed: boolean
}

/**
 * Catches a crash while drawing a part of the screen and shows a calm message instead of a blank page. It never
 * shows the error (no message, no stack): a person cannot act on it, and it may name internals. The details go to
 * the browser console only in development, for whoever is building the app. Give it a `key` that changes with the
 * page so moving to another page starts clean.
 */
class Boundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (import.meta.env.DEV) console.error('Screen error caught by the boundary', error, info.componentStack)
  }

  render() {
    const { t } = this.props
    if (!this.state.failed) return this.props.children
    return (
      <div className="mx-auto max-w-xl py-10">
        <Alert tone="error">
          <p className="font-semibold">{t('recovery.boundary.title')}</p>
          <p className="mt-1">{t('recovery.boundary.hint')}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button onClick={() => this.setState({ failed: false })}>{t('recovery.actions.retry')}</Button>
            <a href="/" className={buttonClasses('secondary')}>
              {t('recovery.actions.dashboard')}
            </a>
            <Button variant="secondary" onClick={() => window.location.reload()}>
              {t('recovery.actions.reload')}
            </Button>
          </div>
        </Alert>
      </div>
    )
  }
}

export const ErrorBoundary = withTranslation()(Boundary)
