import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// `npm run dev:worker` serves audio (R2) and realtime (Durable Object) on 8787.
const WORKER = 'http://127.0.0.1:8787'

export default defineConfig({
  server: {
    proxy: {
      '/audio': WORKER,
      '/ws': { target: WORKER, ws: true },
    },
  },
  plugins: [
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'prompt',
      injectRegister: null,
      includeManifestIcons: false,
      injectManifest: {
        // Tracks are cached on demand by the app, not precached at install.
        globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
      },
      manifest: {
        name: 'Band Companion',
        short_name: 'Band',
        description: 'Backing track player for live sets',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        background_color: '#0b0b0d',
        theme_color: '#0b0b0d',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: '/icons/icon-512-maskable.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
    }),
  ],
})
