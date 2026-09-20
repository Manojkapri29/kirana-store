import { useTranslation } from 'react-i18next'

import { SUPPORTED_LANGUAGES, changeLanguage, type LanguageCode } from '@/i18n'

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()

  return (
    <div
      role="group"
      aria-label={t('layout.language')}
      className="flex rounded-lg border border-slate-300 bg-white p-0.5"
    >
      {SUPPORTED_LANGUAGES.map(({ code, label }) => {
        const active = i18n.resolvedLanguage === code
        return (
          <button
            key={code}
            type="button"
            aria-pressed={active}
            onClick={() => changeLanguage(code as LanguageCode)}
            className={`min-h-10 rounded-md px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 ${
              active ? 'bg-emerald-600 text-white' : 'text-slate-700 hover:bg-slate-100'
            }`}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}
