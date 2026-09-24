import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'

import type { CampaignStatus, SendStatus } from '@/api/crm'
import { Badge, type BadgeTone } from '@/components/ui'

const TABS = [
  { to: '/crm', key: 'overview', end: true },
  { to: '/crm/campaigns', key: 'campaigns', end: false },
  { to: '/crm/reactivation', key: 'reactivation', end: false },
  { to: '/crm/loyalty', key: 'loyalty', end: false },
  { to: '/crm/automation', key: 'automation', end: false },
  { to: '/crm/settings', key: 'settings', end: false },
] as const

/** The sub-navigation shared by every Growth screen. */
export function CrmTabs() {
  const { t } = useTranslation()
  return (
    <nav aria-label={t('crm.tabsLabel')} className="flex flex-wrap gap-2">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) =>
            `inline-flex min-h-10 items-center rounded-lg px-4 text-sm font-medium ${isActive ? 'bg-emerald-700 text-white' : 'bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50'}`
          }
        >
          {t(`crm.tabs.${tab.key}`)}
        </NavLink>
      ))}
    </nav>
  )
}

const CAMPAIGN_TONE: Record<CampaignStatus, BadgeTone> = {
  DRAFT: 'slate', SCHEDULED: 'amber', RUNNING: 'amber', PAUSED: 'amber', COMPLETED: 'green', CANCELLED: 'red',
}
export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  const { t } = useTranslation()
  return <Badge tone={CAMPAIGN_TONE[status]}>{t(`crm.campaignStatus.${status}`)}</Badge>
}

const SEND_TONE: Record<SendStatus, BadgeTone> = {
  SENT: 'green', NOT_CONFIGURED: 'amber', SKIPPED_NO_CONSENT: 'slate', SKIPPED_OPTED_OUT: 'slate', FAILED: 'red',
}
export function SendStatusBadge({ status }: { status: SendStatus }) {
  const { t } = useTranslation()
  return <Badge tone={SEND_TONE[status]}>{t(`crm.sendStatus.${status}`)}</Badge>
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="text-sm text-slate-600">{label}</div>
      <div className="mt-1 text-2xl font-bold text-slate-900">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  )
}
