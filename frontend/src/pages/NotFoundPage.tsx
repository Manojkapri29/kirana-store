import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

export function NotFoundPage() {
  const { t } = useTranslation()

  return (
    <div className="flex flex-col items-center py-20 text-center">
      <h1 className="text-2xl font-bold text-slate-900">{t('notFound.title')}</h1>
      <p className="mt-2 text-slate-600">{t('notFound.message')}</p>
      <Link
        to="/"
        className="mt-6 inline-flex min-h-12 items-center rounded-lg bg-emerald-600 px-5 font-medium text-white hover:bg-emerald-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600"
      >
        {t('notFound.backToDashboard')}
      </Link>
    </div>
  )
}
