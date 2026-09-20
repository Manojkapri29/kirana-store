import { Route, Routes } from 'react-router-dom'

import { InventoryPage } from '@/features/inventory/InventoryPage'
import { ProductDetailPage } from '@/features/products/ProductDetailPage'
import { ProductFormPage } from '@/features/products/ProductFormPage'
import { ProductsPage } from '@/features/products/ProductsPage'
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
        <Route path="products/:id" element={<ProductDetailPage />} />
        <Route path="products/:id/edit" element={<ProductFormPage mode="edit" />} />
        <Route path="inventory" element={<InventoryPage />} />
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
