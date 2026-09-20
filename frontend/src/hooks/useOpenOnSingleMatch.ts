import { useNavigate } from 'react-router-dom'

import { listProducts } from '@/api/products'

/**
 * Barcode scanners type the code and press Enter. If exactly one product matches what was typed, open it.
 * Used by every screen with a product search box, so the behaviour is identical everywhere.
 */
export function useOpenOnSingleMatch(): (text: string) => Promise<void> {
  const navigate = useNavigate()
  return async (text) => {
    const query = text.trim()
    if (!query) return
    try {
      const result = await listProducts({ q: query, status: 'all', limit: 2 })
      if (result.total === 1) void navigate(`/products/${result.items[0].id}`)
    } catch {
      // A failed lookup just leaves the list as it is; the list itself shows load errors.
    }
  }
}
