# AGENTS.md

Backing-track player with live lyrics for a band. One device (leader) plays the audio into the console; other phones follow the lyrics in real time. Vite 8 + vanilla TypeScript page, Cloudflare Worker + Durable Object + R2 backend. No framework, no unit tests, no lint config. `README.md` is accurate; read it for the user-facing workflow.

## Commands

- `npm run dev` — Vite dev server; proxies `/audio` and `/ws` to `npm run dev:worker` (wrangler on 8787). Service worker is NOT active here.
- `npm run dev:worker` — `wrangler dev` with local R2 and a local Durable Object. Fill local R2 with `npm run sync-audio -- --local` first.
- `npm run typecheck` — `wrangler types` (generates the git-ignored `worker-configuration.d.ts`), then `tsc` for the page (`tsconfig.json`: `src`, `shared`) and the worker (`worker/tsconfig.json`). Run after any TS change.
- `npm run build` — typecheck + `vite build`. `npm run preview` builds and serves `dist/` through the worker with the SW active; use it for caching, range requests, offline.
- `npm run sync-audio` — hash WAVs, upload missing ones to R2, rewrite `tracks/audio.json`. `--local` targets the wrangler dev bucket. Exits without touching the manifest when no WAVs are present (CI).
- `npm run deploy` — sync audio + build + `wrangler deploy`. Needs `wrangler login` and the bucket `band-companion-audio` to exist.
- `node scripts/browser-check.mjs [url]` — headless Chrome run-through (leader, lyrics, followers, takeover, offline, tablet) against a running worker. Run it after UI or sync changes; it is the regression suite.

## Tracks

- One folder per song: `tracks/<set>/<NN - Title>/` with one `.wav` (git-ignored) and optionally one `.lrc`/`.txt` (committed). Track id = folder path, used in the realtime protocol; renaming a folder changes the id.
- `src/tracks.ts` reads `tracks/audio.json` for audio (`/audio/<hash>.wav`) and globs lyrics with `import.meta.glob(..., { query: '?raw' })`. A song needs an audio entry to be listed; lyrics without audio only warn.
- Audio never goes through the Vite build: Cloudflare rejects assets over 25 MB. Do not add audio globs or move audio into `public/`.
- Unicode: macOS reports folder names as NFD, git (`core.precomposeunicode`) stores and checks them out as NFC. `sync-audio.mjs` and `tracks.ts` normalize every folder key to NFC; keep it that way or a fresh checkout pairs no lyrics with the audio. Compare titles with `.normalize('NFC')` in tests.

## Worker

- `worker/index.ts`: `/audio/:key` → R2 with range passthrough (`object.range` properties are checked by type, the local runtime leaves unused ones `undefined`), `/ws` → `Room` DO, else `env.ASSETS.fetch` (SPA fallback).
- `worker/room.ts`: single room `main`, WebSocket Hibernation API, tags `leader`/`follower`, auto-pong for `ping`. Last state persisted in DO storage. A new authenticated leader closes previous leaders with `4002`; a wrong key is accepted then closed with `4001` so the client can tell it apart from a network failure.
- `LEADER_KEY` default is in `wrangler.jsonc` `vars`; `wrangler secret put LEADER_KEY` overrides it. `shared/protocol.ts` is imported by both page and worker.

## Roles / realtime (`src/sync.ts`, `src/role.ts`, `src/main.ts`)

- Role is local: a stored key means leader. The leader works fully offline; the socket only broadcasts. Only close codes `4001`/`4002` clear the key and demote; connection loss just shows "Leader · offline".
- Demotion must clear the key, otherwise two devices with the key would take turns demoting each other on reconnect.
- `Broadcaster.sync()` runs on every player state change but only sends on track/playing changes or seeks (> 1 s from expected); a 2 s heartbeat covers drift. `Follower.now()` extrapolates on the local clock while `playing`.
- `main.ts` mounts `UI` (leader) or `FollowUI` (follower) into `#app` and swaps them at runtime; `Player` and the `<audio>` element are created once.
- `src/monitor.ts` (follower audio, "Muted / Listening" chip) uses the second `<audio id="monitor">`, never the leader's element. Off by default and loads nothing until toggled; `toggle()` must run inside the tap so iOS unlocks the element (`unlock()` does play+pause when there is nothing to play yet). It seeks on > 0.5 s drift from `Follower.now()`; the leader's 2 s heartbeat is what triggers corrections. Media-session handlers are claimed by whichever mode mounts (`Player.attachMediaSession()` vs `Monitor`).

## Offline / service worker

- `vite-plugin-pwa` in `injectManifest` mode; the SW source is `src/sw.ts`. Audio is excluded from `globPatterns` deliberately.
- The page writes audio into Cache Storage (`src/offline.ts`); the SW only reads it and matches on `^/audio/`. Cache name `tracks-v1` is duplicated in both files and must stay in sync.
- `workbox-range-requests` slices cached responses; Safari requests audio in byte ranges.
- `registerType: 'prompt'`: updates show a "Reload" toast instead of auto-reloading. Keep it that way.

## Player / UI constraints

- Tapping a song loads it (`Player.select`) and opens the lyrics view; Play / Pause / Restart act on the loaded track. Restart is `seek(0)`; seeks preserve playing/paused state.
- Leader-only seeking: `.seek` (slider around the 6 px bar, negative margins give a 44 px target) previews during the drag and commits on release; `updatePlayer` skips the position while `scrubbing`. `LyricsView({ onSeek })` makes timed lines `role=button`; the follower constructs it without `onSeek`. `touchmove`/`wheel` on the lyrics hold auto-scroll for 4 s (`READ_AHEAD_HOLD`) so reading ahead is not fought; a tap does not trigger the hold.
- `audio.play()` must be called synchronously inside the tap handler (iOS). No `await` before it.
- Screen Wake Lock: WebKit grants `wakeLock.request()` only with transient user activation (then remembers it for the page). On touch, `pointerdown` has no activation, `pointerup`/`click` do — request from those. `wakelock.ts` retries on any later `pointerup`/`keydown` while wanted and unheld, so a refused request heals on the next tap; it is re-acquired on `visibilitychange`.
- `[hidden] { display: none !important }` is relied on; views are switched with `#app.view-songs` / `#app.view-lyrics` classes instead of `hidden` because the ≥ 700 px layout shows both. `#app.follow` is the follower layout.
- `#app` grid rows are pinned with `grid-row` on each child because the set bar can be `display: none`.
- `LyricsView` uses `offsetTop` relative to `.lyrics`, which must stay `position: relative`.
- Mobile first: safe-area insets, `100dvh`, no hover styles, ≥ 48 px targets. UI copy uses real typographic characters (’ – — … ×) via `\u` escapes in TS.

## Verifying layouts without a device

Plain `chrome --headless --screenshot` cannot do phone widths. `scripts/cdp.mjs` wraps the DevTools protocol (`Emulation.setDeviceMetricsOverride` with `mobile: true`, one browser context per simulated device so roles stay independent, `Network.emulateNetworkConditions` for offline). `scripts/browser-check.mjs` is the example to extend.
