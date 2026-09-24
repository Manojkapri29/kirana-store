import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from '@/app/App'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { MUTATIONS_RETRY, queryRetryDelay, shouldRetryQuery } from '@/lib/retryPolicy'
import { i18nReady } from '@/i18n'
import { registerServiceWorker } from '@/pwa/register'

import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    // Reading is retried a little when the failure can go away by itself; writing is never retried automatically.
    queries: { staleTime: 10_000, retry: shouldRetryQuery, retryDelay: queryRetryDelay, refetchOnWindowFocus: false },
    mutations: { retry: MUTATIONS_RETRY },
  },
})

void i18nReady.then(() => createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </ErrorBoundary>
    </QueryClientProvider>
  </StrictMode>,
))

registerServiceWorker()
