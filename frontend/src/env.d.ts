/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API. Empty in development (requests use the Vite proxy). */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
