import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { loadEnv, type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

/**
 * PWA release files. `precache-manifest.json` lists every script and stylesheet of this release; `sw.js` gets a build id that is a hash of
 * that list, so each release installs as a new worker and the previous release's cache is removed.
 */
function pwaRelease(): Plugin {
  let files: string[] = []
  let outDir = 'dist'
  return {
    name: 'pwa-release',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir
    },
    generateBundle(_options, bundle) {
      files = Object.keys(bundle).filter((name) => /\.(js|css)$/.test(name)).map((name) => `/${name}`).sort()
      this.emitFile({ type: 'asset', fileName: 'precache-manifest.json', source: JSON.stringify({ files }) })
    },
    closeBundle() {
      const id = createHash('sha256').update(files.join('|')).digest('hex').slice(0, 12)
      const path = `${outDir}/sw.js`
      try {
        writeFileSync(path, readFileSync(path, 'utf8').replace('__BUILD_ID__', id))
      } catch {
        // No worker in the output: nothing to stamp.
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const backendUrl = env.VITE_DEV_API_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react(), tailwindcss(), pwaRelease()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    test: {
      environment: 'jsdom',
      globals: false,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
    server: {
      port: 5173,
      strictPort: true, // the backend's default CORS origin expects exactly this port
      // In development the browser talks to Vite, which forwards API calls to FastAPI.
      proxy: {
        '/api': backendUrl,
        '/health': backendUrl,
      },
    },
  }
})
