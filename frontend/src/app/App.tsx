import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'

import { Spinner } from '@/components/ui'
import { AcceptInvitationPage } from '@/features/auth/AcceptInvitationPage'
import { AuthGate } from '@/features/auth/AuthGate'
import { AppLayout } from '@/layouts/AppLayout'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { NAV_ITEMS } from './navigation'

/** Every screen is loaded when first opened, so the first page a phone downloads is small (see docs/PWA_OFFLINE.md). */
const OfflinePage = lazy(() => import('@/features/offline/OfflinePage').then((m) => ({ default: m.OfflinePage })))
const AdminConsolePage = lazy(() => import('@/features/admin/AdminConsolePage').then((m) => ({ default: m.AdminConsolePage })))
const AssistantPage = lazy(() => import('@/features/assistant/AssistantPage').then((m) => ({ default: m.AssistantPage })))
const AutomationPage = lazy(() => import('@/features/crm/AutomationPage').then((m) => ({ default: m.AutomationPage })))
const BuilderPage = lazy(() => import('@/features/analytics/BuilderPage').then((m) => ({ default: m.BuilderPage })))
const CampaignDetailPage = lazy(() => import('@/features/crm/CampaignDetailPage').then((m) => ({ default: m.CampaignDetailPage })))
const CampaignsPage = lazy(() => import('@/features/crm/CampaignsPage').then((m) => ({ default: m.CampaignsPage })))
const CashPage = lazy(() => import('@/features/finance/CashPage').then((m) => ({ default: m.CashPage })))
const ChangePasswordPage = lazy(() => import('@/features/auth/ChangePasswordPage').then((m) => ({ default: m.ChangePasswordPage })))
const ComingSoonPage = lazy(() => import('@/pages/ComingSoonPage').then((m) => ({ default: m.ComingSoonPage })))
const CrmDashboardPage = lazy(() => import('@/features/crm/CrmDashboardPage').then((m) => ({ default: m.CrmDashboardPage })))
const CrmSettingsPage = lazy(() => import('@/features/crm/CrmSettingsPage').then((m) => ({ default: m.CrmSettingsPage })))
const CustomerCrmPage = lazy(() => import('@/features/crm/CustomerCrmPage').then((m) => ({ default: m.CustomerCrmPage })))
const CustomerDetailPage = lazy(() => import('@/features/customers/CustomerDetailPage').then((m) => ({ default: m.CustomerDetailPage })))
const CustomerFormPage = lazy(() => import('@/features/customers/CustomerFormPage').then((m) => ({ default: m.CustomerFormPage })))
const CustomersPage = lazy(() => import('@/features/customers/CustomersPage').then((m) => ({ default: m.CustomersPage })))
const DashboardPage = lazy(() => import('@/pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const DrillPage = lazy(() => import('@/features/analytics/DrillPage').then((m) => ({ default: m.DrillPage })))
const ExecutivePage = lazy(() => import('@/features/analytics/ExecutivePage').then((m) => ({ default: m.ExecutivePage })))
const ExpensesPage = lazy(() => import('@/features/finance/ExpensesPage').then((m) => ({ default: m.ExpensesPage })))
const FinanceDashboardPage = lazy(() => import('@/features/finance/FinanceDashboardPage').then((m) => ({ default: m.FinanceDashboardPage })))
const FinanceReportsPage = lazy(() => import('@/features/finance/FinanceReportsPage').then((m) => ({ default: m.FinanceReportsPage })))
const FinanceSettingsPage = lazy(() => import('@/features/finance/FinanceSettingsPage').then((m) => ({ default: m.FinanceSettingsPage })))
const InsightsPage = lazy(() => import('@/features/insights/InsightsPage').then((m) => ({ default: m.InsightsPage })))
const IntegrationsPage = lazy(() => import('@/features/integrations/IntegrationsPage').then((m) => ({ default: m.IntegrationsPage })))
const InventoryPage = lazy(() => import('@/features/inventory/InventoryPage').then((m) => ({ default: m.InventoryPage })))
const LedgerPage = lazy(() => import('@/features/finance/LedgerPage').then((m) => ({ default: m.LedgerPage })))
const LoyaltyPage = lazy(() => import('@/features/crm/LoyaltyPage').then((m) => ({ default: m.LoyaltyPage })))
const NotificationsPage = lazy(() => import('@/features/notifications/NotificationsPage').then((m) => ({ default: m.NotificationsPage })))
const ObservationsPage = lazy(() => import('@/features/analytics/InsightsPage').then((m) => ({ default: m.ObservationsPage })))
const PeriodsPage = lazy(() => import('@/features/finance/PeriodsPage').then((m) => ({ default: m.PeriodsPage })))
const ProductDetailPage = lazy(() => import('@/features/products/ProductDetailPage').then((m) => ({ default: m.ProductDetailPage })))
const ProductFormPage = lazy(() => import('@/features/products/ProductFormPage').then((m) => ({ default: m.ProductFormPage })))
const ProductFromPhotoPage = lazy(() => import('@/features/products/ProductFromPhotoPage').then((m) => ({ default: m.ProductFromPhotoPage })))
const ProductsPage = lazy(() => import('@/features/products/ProductsPage').then((m) => ({ default: m.ProductsPage })))
const PromotionDetailPage = lazy(() => import('@/features/promotions/PromotionDetailPage').then((m) => ({ default: m.PromotionDetailPage })))
const PromotionFormPage = lazy(() => import('@/features/promotions/PromotionFormPage').then((m) => ({ default: m.PromotionFormPage })))
const PromotionsPage = lazy(() => import('@/features/promotions/PromotionsPage').then((m) => ({ default: m.PromotionsPage })))
const PurchaseDetailPage = lazy(() => import('@/features/purchases/PurchaseDetailPage').then((m) => ({ default: m.PurchaseDetailPage })))
const PurchaseFormPage = lazy(() => import('@/features/purchases/PurchaseFormPage').then((m) => ({ default: m.PurchaseFormPage })))
const PurchaseReturnFormPage = lazy(() => import('@/features/returns/PurchaseReturnFormPage').then((m) => ({ default: m.PurchaseReturnFormPage })))
const PurchasesPage = lazy(() => import('@/features/purchases/PurchasesPage').then((m) => ({ default: m.PurchasesPage })))
const QuickSaleDetailPage = lazy(() => import('@/features/quickSales/QuickSaleDetailPage').then((m) => ({ default: m.QuickSaleDetailPage })))
const QuickSaleFormPage = lazy(() => import('@/features/quickSales/QuickSaleFormPage').then((m) => ({ default: m.QuickSaleFormPage })))
const QuickSalesPage = lazy(() => import('@/features/quickSales/QuickSalesPage').then((m) => ({ default: m.QuickSalesPage })))
const ReactivationPage = lazy(() => import('@/features/crm/ReactivationPage').then((m) => ({ default: m.ReactivationPage })))
const ReconciliationPage = lazy(() => import('@/features/finance/ReconciliationPage').then((m) => ({ default: m.ReconciliationPage })))
const ReportsPage = lazy(() => import('@/features/analytics/ReportsPage').then((m) => ({ default: m.ReportsPage })))
const ReturnDetailPage = lazy(() => import('@/features/returns/ReturnDetailPage').then((m) => ({ default: m.ReturnDetailPage })))
const ReturnsPage = lazy(() => import('@/features/returns/ReturnsPage').then((m) => ({ default: m.ReturnsPage })))
const RolesPage = lazy(() => import('@/features/staff/RolesPage').then((m) => ({ default: m.RolesPage })))
const SaleDetailPage = lazy(() => import('@/features/sales/SaleDetailPage').then((m) => ({ default: m.SaleDetailPage })))
const SaleFormPage = lazy(() => import('@/features/sales/SaleFormPage').then((m) => ({ default: m.SaleFormPage })))
const SalesPage = lazy(() => import('@/features/sales/SalesPage').then((m) => ({ default: m.SalesPage })))
const SalesReportPage = lazy(() => import('@/features/reports/SalesReportPage').then((m) => ({ default: m.SalesReportPage })))
const SalesReturnFormPage = lazy(() => import('@/features/returns/SalesReturnFormPage').then((m) => ({ default: m.SalesReturnFormPage })))
const SavedReportsPage = lazy(() => import('@/features/analytics/SavedReportsPage').then((m) => ({ default: m.SavedReportsPage })))
const SchedulesPage = lazy(() => import('@/features/analytics/SchedulesPage').then((m) => ({ default: m.SchedulesPage })))
const SettingsPage = lazy(() => import('@/features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const StaffPage = lazy(() => import('@/features/staff/StaffPage').then((m) => ({ default: m.StaffPage })))
const SubscriptionPage = lazy(() => import('@/features/subscription/SubscriptionPage').then((m) => ({ default: m.SubscriptionPage })))
const SupplierDetailPage = lazy(() => import('@/features/suppliers/SupplierDetailPage').then((m) => ({ default: m.SupplierDetailPage })))
const SupplierFormPage = lazy(() => import('@/features/suppliers/SupplierFormPage').then((m) => ({ default: m.SupplierFormPage })))
const SuppliersPage = lazy(() => import('@/features/suppliers/SuppliersPage').then((m) => ({ default: m.SuppliersPage })))

export function App() {
  return (
    <Suspense fallback={<Spinner />}>
    <Routes>
      <Route path="accept-invitation" element={<AcceptInvitationPage />} />
      <Route
        element={
          <AuthGate>
            <AppLayout />
          </AuthGate>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="products" element={<ProductsPage />} />
        <Route path="products/new" element={<ProductFormPage mode="create" />} />
        <Route path="products/from-photo" element={<ProductFromPhotoPage />} />
        <Route path="products/:id" element={<ProductDetailPage />} />
        <Route path="products/:id/edit" element={<ProductFormPage mode="edit" />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="purchases" element={<PurchasesPage />} />
        <Route path="purchases/new" element={<PurchaseFormPage mode="create" />} />
        <Route path="purchases/:id" element={<PurchaseDetailPage />} />
        <Route path="purchases/:id/edit" element={<PurchaseFormPage mode="edit" />} />
        <Route path="purchases/:id/return" element={<PurchaseReturnFormPage />} />
        <Route path="assistant" element={<AssistantPage />} />
        <Route path="returns" element={<ReturnsPage />} />
        <Route path="returns/sales/:id" element={<ReturnDetailPage kind="sales" />} />
        <Route path="returns/purchases/:id" element={<ReturnDetailPage kind="purchases" />} />
        <Route path="sales" element={<SalesPage />} />
        <Route path="sales/new" element={<SaleFormPage mode="create" />} />
        <Route path="sales/:id" element={<SaleDetailPage />} />
        <Route path="sales/:id/edit" element={<SaleFormPage mode="edit" />} />
        <Route path="sales/:id/return" element={<SalesReturnFormPage />} />
        <Route path="quick-sales" element={<QuickSalesPage />} />
        <Route path="quick-sales/new" element={<QuickSaleFormPage mode="create" />} />
        <Route path="quick-sales/:id" element={<QuickSaleDetailPage />} />
        <Route path="quick-sales/:id/edit" element={<QuickSaleFormPage mode="edit" />} />
        <Route path="promotions" element={<PromotionsPage />} />
        <Route path="promotions/new" element={<PromotionFormPage mode="create" />} />
        <Route path="promotions/:id" element={<PromotionDetailPage />} />
        <Route path="promotions/:id/edit" element={<PromotionFormPage mode="edit" />} />
        <Route path="reports" element={<SalesReportPage />} />
        <Route path="plan" element={<SubscriptionPage />} />
        <Route path="customers" element={<CustomersPage />} />
        <Route path="customers/new" element={<CustomerFormPage mode="create" />} />
        <Route path="customers/:id" element={<CustomerDetailPage />} />
        <Route path="customers/:id/crm" element={<CustomerCrmPage />} />
        <Route path="offline" element={<OfflinePage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="analytics" element={<ExecutivePage />} />
        <Route path="analytics/reports" element={<ReportsPage />} />
        <Route path="analytics/drill" element={<DrillPage />} />
        <Route path="analytics/builder" element={<BuilderPage />} />
        <Route path="analytics/saved" element={<SavedReportsPage />} />
        <Route path="analytics/schedules" element={<SchedulesPage />} />
        <Route path="analytics/insights" element={<ObservationsPage />} />
        <Route path="finance" element={<FinanceDashboardPage />} />
        <Route path="finance/expenses" element={<ExpensesPage />} />
        <Route path="finance/cash" element={<CashPage />} />
        <Route path="finance/ledger" element={<LedgerPage />} />
        <Route path="finance/reports" element={<FinanceReportsPage />} />
        <Route path="finance/reconciliation" element={<ReconciliationPage />} />
        <Route path="finance/periods" element={<PeriodsPage />} />
        <Route path="finance/settings" element={<FinanceSettingsPage />} />
        <Route path="crm" element={<CrmDashboardPage />} />
        <Route path="crm/campaigns" element={<CampaignsPage />} />
        <Route path="crm/campaigns/:id" element={<CampaignDetailPage />} />
        <Route path="crm/reactivation" element={<ReactivationPage />} />
        <Route path="crm/loyalty" element={<LoyaltyPage />} />
        <Route path="crm/automation" element={<AutomationPage />} />
        <Route path="crm/settings" element={<CrmSettingsPage />} />
        <Route path="customers/:id/edit" element={<CustomerFormPage mode="edit" />} />
        <Route path="suppliers" element={<SuppliersPage />} />
        <Route path="suppliers/new" element={<SupplierFormPage mode="create" />} />
        <Route path="suppliers/:id" element={<SupplierDetailPage />} />
        <Route path="suppliers/:id/edit" element={<SupplierFormPage mode="edit" />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="staff" element={<StaffPage />} />
        <Route path="staff/roles" element={<RolesPage />} />
        <Route path="account/password" element={<ChangePasswordPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route path="admin" element={<AdminConsolePage />} />
        {/* Modules that are not built yet render a placeholder. Each phase swaps in the real page. */}
        {NAV_ITEMS.filter((item) => item.phase !== undefined).map((item) => (
          <Route key={item.id} path={item.path} element={<ComingSoonPage item={item} />} />
        ))}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
    </Suspense>
  )
}
