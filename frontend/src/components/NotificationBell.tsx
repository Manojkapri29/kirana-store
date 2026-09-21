import { useQuery } from '@tanstack/react-query'
import { Bell } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { getUnreadCount } from '@/api/notifications'

/** The header's bell with the unread count. It refreshes quietly; a failure just hides the count, never breaks the page. */
export function NotificationBell() {
  const { t } = useTranslation()
  const { data } = useQuery({ queryKey: ['notifications', 'unread'], queryFn: getUnreadCount, refetchInterval: 60_000, retry: false })
  const unread = data?.unread ?? 0
  return (
    <Link
      to="/notifications"
      aria-label={unread > 0 ? t('notifications.bellUnread', { count: unread }) : t('notifications.bell')}
      className="relative flex size-12 items-center justify-center rounded-lg text-slate-700 hover:bg-slate-100"
    >
      <Bell aria-hidden="true" className="size-5" />
      {unread > 0 && (
        <span className="absolute right-1.5 top-1.5 min-w-5 rounded-full bg-red-600 px-1 text-center text-xs font-semibold leading-5 text-white">
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </Link>
  )
}
