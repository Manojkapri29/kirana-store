import { Menu } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { BackendStatusBadge } from '@/components/BackendStatusBadge'
import { LanguageSwitcher } from '@/components/LanguageSwitcher'

interface HeaderProps {
  onMenuClick: () => void
}

export function Header({ onMenuClick }: HeaderProps) {
  const { t } = useTranslation()

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center justify-between gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur sm:px-6">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onMenuClick}
          aria-label={t('layout.openMenu')}
          className="flex size-12 items-center justify-center rounded-lg text-slate-700 hover:bg-slate-100 lg:hidden"
        >
          <Menu aria-hidden="true" className="size-6" />
        </button>
        <span className="font-semibold text-slate-900 lg:hidden">{t('app.name')}</span>
      </div>

      <div className="flex items-center gap-2 sm:gap-3">
        <BackendStatusBadge />
        <LanguageSwitcher />
      </div>
    </header>
  )
}
