import { Route, Routes } from 'react-router-dom'

import { CustomerDetailPage } from '@/features/customers/CustomerDetailPage'
import { CustomerFormPage } from '@/features/customers/CustomerFormPage'
import { CustomersPage } from '@/features/customers/CustomersPage'
import { InventoryPage } from '@/features/inventory/InventoryPage'
import { ProductDetailPage } from '@/features/products/ProductDetailPage'
import { ProductFormPage } from '@/features/products/ProductFormPage'
import { ProductFromPhotoPage } from '@/features/products/ProductFromPhotoPage'
import { ProductsPage } from '@/features/products/ProductsPage'
import { PromotionDetailPage } from '@/features/promotions/PromotionDetailPage'
import { PromotionFormPage } from '@/features/promotions/PromotionFormPage'
import { PromotionsPage } from '@/features/promotions/PromotionsPage'
import { AssistantPage } from '@/features/assistant/AssistantPage'
import { PurchaseReturnFormPage } from '@/features/returns/PurchaseReturnFormPage'
import { ReturnDetailPage } from '@/features/returns/ReturnDetailPage'
import { ReturnsPage } from '@/features/returns/ReturnsPage'
import { SalesReturnFormPage } from '@/features/returns/SalesReturnFormPage'
import { SalesReportPage } from '@/features/reports/SalesReportPage'
import { QuickSaleDetailPage } from '@/features/quickSales/QuickSaleDetailPage'
import { QuickSaleFormPage } from '@/features/quickSales/QuickSaleFormPage'
import { QuickSalesPage } from '@/features/quickSales/QuickSalesPage'
import { SubscriptionPage } from '@/features/subscription/SubscriptionPage'
import { SaleDetailPage } from '@/features/sales/SaleDetailPage'
import { SaleFormPage } from '@/features/sales/SaleFormPage'
import { SalesPage } from '@/features/sales/SalesPage'
import { PurchaseDetailPage } from '@/features/purchases/PurchaseDetailPage'
import { PurchaseFormPage } from '@/features/purchases/PurchaseFormPage'
import { PurchasesPage } from '@/features/purchases/PurchasesPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { SupplierDetailPage } from '@/features/suppliers/SupplierDetailPage'
import { SupplierFormPage } from '@/features/suppliers/SupplierFormPage'
import { SuppliersPage } from '@/features/suppliers/SuppliersPage'
import { AppLayout } from '@/layouts/AppLayout'
import { ComingSoonPage } from '@/pages/ComingSoonPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { NotFoundPage } from '@/pages/NotFoundPage'

import { NAV_ITEMS } from './navigation'

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
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
        <Route path="customers/:id/edit" element={<CustomerFormPage mode="edit" />} />
        <Route path="suppliers" element={<SuppliersPage />} />
        <Route path="suppliers/new" element={<SupplierFormPage mode="create" />} />
        <Route path="suppliers/:id" element={<SupplierDetailPage />} />
        <Route path="suppliers/:id/edit" element={<SupplierFormPage mode="edit" />} />
        <Route path="settings" element={<SettingsPage />} />
        {/* Modules that are not built yet render a placeholder. Each phase swaps in the real page. */}
        {NAV_ITEMS.filter((item) => item.phase !== undefined).map((item) => (
          <Route key={item.id} path={item.path} element={<ComingSoonPage item={item} />} />
        ))}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
