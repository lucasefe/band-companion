# AGENTS.md

Backing-track player for a live band, used on an iPhone / iPad mini plugged into a mixing console. Static PWA: Vite 8 + vanilla TypeScript, no framework, no tests, no lint config. `README.md` is accurate; read it for the user-facing workflow.

## Commands

- `npm run dev` — Vite dev server. Service worker is NOT active here; offline/update behaviour only exists in `preview`.
- `npm run build` — runs `prebuild` (`scripts/convert.mjs`), then `tsc --noEmit`, then `vite build`. This is the only typecheck; run it after any TS change.
- `npm run preview` — serves `dist/` with the service worker. Use this to test caching, range requests, offline.
- `npm run convert` — ffmpeg WAV/AIFF/FLAC → 320 kbps MP3 next to the source, skipped when up to date. No-op (exit 0) when there are no lossless files, so Cloudflare builds (no ffmpeg) are fine.
- `npm run deploy` — build + `wrangler deploy`. This is Cloudflare **Workers static assets** (`wrangler.jsonc`, `assets.directory: ./dist`), not Pages. Node 22 is pinned by `.node-version`; Vite 8 needs ≥ 22.12.

## Tracks

- `src/tracks.ts` discovers files at build time with `import.meta.glob('/tracks/**/*.{mp3,m4a}')`. Subfolder = set, `NN - Title` prefix = order (stripped from display). One set hides the set bar.
- Only mp3/m4a are listed on purpose: lossless sources sit next to their MP3 and are git-ignored (`tracks/**/*.wav|aif|aiff|flac`). Commit the MP3s.
- Cloudflare rejects assets over 25 MB. That is why WAV is converted rather than deployed.
- Built track URLs are hashed (`/assets/<name>-<hash>.mp3`), which is what makes cache invalidation work. Do not move tracks to `public/`.

## Offline / service worker

- `vite-plugin-pwa` in `injectManifest` mode; the SW source is `src/sw.ts`. Tracks are excluded from `globPatterns` deliberately — precaching 100s of MB at install fails the whole install.
- The page writes tracks into Cache Storage (`src/offline.ts`, "Download N tracks" chip); the SW only reads that cache. The cache name `tracks-v1` is duplicated in both files and must stay in sync.
- The SW slices cached responses with `workbox-range-requests`. Safari requests audio in byte ranges; without this, cached tracks do not play on iOS.
- `registerType: 'prompt'`: updates show a "Reload" toast instead of auto-reloading. Keep it that way; an auto-reload mid-song at a gig is the failure mode being avoided.

## Player / UI constraints

- Tapping a song only loads it (`Player.select`); Play / Pause / Restart act on the loaded track. Restart rewinds and preserves playing/paused state; a finished track rewinds and waits.
- `audio.play()` must be called synchronously inside the tap handler (iOS gesture rule). Do not put `await` before it.
- Screen Wake Lock is requested on the first tap and re-acquired on `visibilitychange`. There is no hidden-video fallback by design; the chip reports "Can’t keep awake" instead.
- Components use the `hidden` attribute and `[hidden] { display: none !important }` in `styles.css`, because they also set `display: grid/flex`. Keep that rule.
- `#app` grid rows are pinned with `grid-row` on each child because the set bar can be `display: none`; without pinning the footer takes the `1fr` row and stretches.
- Mobile first: safe-area insets, `100dvh`, no hover styles, ≥ 48 px targets. The ≥ 700 px media query adds two columns and moves the transport beside the title; that's the whole "tablet" layout.
- UI copy uses real typographic characters (’ – — … ×) via `\u` escapes in TS and entities in HTML.

## Verifying layouts without a device

Plain `chrome --headless --screenshot --window-size=402,…` does not work: headless Chrome enforces a ~500 px minimum window, so phone layouts render clipped. Use the DevTools protocol (`--remote-debugging-port` + `Emulation.setDeviceMetricsOverride` with `mobile: true`) to emulate 402×874 (iPhone) and 1024×744 (iPad mini), then drive `.song`, `.transport-play/-pause/-restart`, `.chip-offline` via `Runtime.evaluate`. `Network.emulateNetworkConditions({ offline: true })` after a download is the offline test.
