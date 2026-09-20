import type { ParseKeys } from 'i18next'
import {
  BarChart3,
  Boxes,
  LayoutDashboard,
  PackageSearch,
  ReceiptText,
  ShoppingCart,
  Truck,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react'

export interface NavItem {
  /** Stable identifier, also used as the route key. */
  id: string
  path: string
  icon: LucideIcon
  labelKey: ParseKeys
  /** Short explanation shown on the "coming soon" page. */
  descriptionKey?: ParseKeys
  /** Roadmap phase in which the real module is built. Absent for finished screens. */
  phase?: number
}

export const NAV_ITEMS: readonly NavItem[] = [
  { id: 'dashboard', path: '/', icon: LayoutDashboard, labelKey: 'nav.dashboard' },
  {
    id: 'products',
    path: '/products',
    icon: PackageSearch,
    labelKey: 'nav.products',
    descriptionKey: 'modules.products.description',
    phase: 3,
  },
  {
    id: 'inventory',
    path: '/inventory',
    icon: Boxes,
    labelKey: 'nav.inventory',
    descriptionKey: 'modules.inventory.description',
    phase: 10,
  },
  {
    id: 'purchases',
    path: '/purchases',
    icon: Truck,
    labelKey: 'nav.purchases',
    descriptionKey: 'modules.purchases.description',
    phase: 5,
  },
  {
    id: 'sales',
    path: '/sales',
    icon: ShoppingCart,
    labelKey: 'nav.sales',
    descriptionKey: 'modules.sales.description',
    phase: 7,
  },
  {
    id: 'customers',
    path: '/customers',
    icon: Users,
    labelKey: 'nav.customers',
    descriptionKey: 'modules.customers.description',
    phase: 6,
  },
  {
    id: 'suppliers',
    path: '/suppliers',
    icon: ReceiptText,
    labelKey: 'nav.suppliers',
    descriptionKey: 'modules.suppliers.description',
    phase: 4,
  },
  {
    id: 'expenses',
    path: '/expenses',
    icon: Wallet,
    labelKey: 'nav.expenses',
    descriptionKey: 'modules.expenses.description',
    phase: 11,
  },
  {
    id: 'reports',
    path: '/reports',
    icon: BarChart3,
    labelKey: 'nav.reports',
    descriptionKey: 'modules.reports.description',
    phase: 13,
  },
]
