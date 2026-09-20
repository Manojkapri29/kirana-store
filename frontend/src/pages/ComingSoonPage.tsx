import { useTranslation } from 'react-i18next'

import type { NavItem } from '@/app/navigation'

/** Placeholder for a module that is on the roadmap but not built yet. */
export function ComingSoonPage({ item }: { item: NavItem }) {
  const { t } = useTranslation()
  const Icon = item.icon

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">{t(item.labelKey)}</h1>

      <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center">
        <span className="flex size-14 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700">
          <Icon aria-hidden="true" className="size-7" />
        </span>
        {item.descriptionKey && (
          <p className="mt-4 max-w-md text-lg text-slate-800">{t(item.descriptionKey)}</p>
        )}
        <p className="mt-2 max-w-md text-slate-600">
          {t('comingSoon.message', { phase: item.phase })}
        </p>
        <span className="mt-5 rounded-full bg-slate-100 px-3 py-1 text-sm font-medium text-slate-700">
          {t('comingSoon.badge', { phase: item.phase })}
        </span>
      </div>
    </div>
  )
}
