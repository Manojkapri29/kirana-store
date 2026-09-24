import type { ParseKeys } from 'i18next'
import {
  BarChart3,
  Boxes,
  CreditCard,
  HeartHandshake,
  Tag,
  Zap,
  LayoutDashboard,
  PackageSearch,
  ReceiptText,
  Sparkles,
  TrendingUp,
  Undo2,
  Settings,
  ShoppingCart,
  Truck,
  UserCog,
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
  /** The permission that shows this item. Only a convenience: the server decides what a person may do. */
  permission?: string
}

export const NAV_ITEMS: readonly NavItem[] = [
  { id: 'dashboard', path: '/', icon: LayoutDashboard, labelKey: 'nav.dashboard' },
  {
    id: 'products', permission: 'PRODUCT_VIEW',
    path: '/products',
    icon: PackageSearch,
    labelKey: 'nav.products',
  },
  {
    id: 'inventory', permission: 'INVENTORY_VIEW',
    path: '/inventory',
    icon: Boxes,
    labelKey: 'nav.inventory',
  },
  {
    id: 'purchases', permission: 'PURCHASE_VIEW',
    path: '/purchases',
    icon: Truck,
    labelKey: 'nav.purchases',
  },
  {
    id: 'sales', permission: 'SALE_VIEW',
    path: '/sales',
    icon: ShoppingCart,
    labelKey: 'nav.sales',
  },
  {
    id: 'quick-sales', permission: 'SALE_VIEW',
    path: '/quick-sales',
    icon: Zap,
    labelKey: 'nav.quickSales',
  },
  { id: 'assistant', permission: 'AI_USE', path: '/assistant', icon: Sparkles, labelKey: 'nav.assistant' },
  { id: 'returns', permission: 'SALE_VIEW', path: '/returns', icon: Undo2, labelKey: 'nav.returns' },
  {
    id: 'promotions', permission: 'PROMOTION_VIEW',
    path: '/promotions',
    icon: Tag,
    labelKey: 'nav.offers',
  },
  {
    id: 'customers', permission: 'CUSTOMER_VIEW',
    path: '/customers',
    icon: Users,
    labelKey: 'nav.customers',
  },
  { id: 'crm', permission: 'CRM_ANALYTICS_VIEW', path: '/crm', icon: HeartHandshake, labelKey: 'nav.crm' },
  {
    id: 'suppliers', permission: 'SUPPLIER_VIEW',
    path: '/suppliers',
    icon: ReceiptText,
    labelKey: 'nav.suppliers',
  },
  {
    id: 'expenses',
    path: '/expenses',
    icon: Wallet,
    labelKey: 'nav.expenses',
    descriptionKey: 'modules.expenses.description',
    phase: 11,
  },
  { id: 'reports', permission: 'REPORT_VIEW', path: '/reports', icon: BarChart3, labelKey: 'nav.reports' },
  { id: 'insights', permission: 'REPORT_VIEW', path: '/insights', icon: TrendingUp, labelKey: 'nav.insights' },
  { id: 'plan', permission: 'SUBSCRIPTION_VIEW', path: '/plan', icon: CreditCard, labelKey: 'nav.plan' },
  { id: 'staff', path: '/staff', icon: UserCog, labelKey: 'nav.staff', permission: 'STAFF_VIEW' },
  { id: 'settings', path: '/settings', icon: Settings, labelKey: 'nav.settings' },
]
