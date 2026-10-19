# Band Companion

A backing‑track player for live sets. Open it on a phone or iPad plugged into the console, tap a song, play along. One track at a time; tapping another song stops the current one; tapping the playing song stops it.

It is a static web app (Vite + TypeScript) that runs as a Home Screen web app on iOS. It keeps the screen awake while a track is playing and can store every track on the device so it works with no internet at the venue.

## Adding songs

Drop audio files into `tracks/`, one subfolder per set:

```
tracks/
├── 1 - First set/
│   ├── 01 - Sing Sing Sing.mp3
│   └── 02 - In the Mood.mp3
└── 2 - Second set/
    └── 01 - Moonlight Serenade.mp3
```

- A leading number controls the order and is stripped from the name shown.
- Folders become set tabs. Files placed directly in `tracks/` go into a set called “Songs”. With a single set the tabs are hidden.
- Supported: `.mp3`, `.m4a`, `.aac`, `.wav`, `.ogg`. Use MP3 or AAC. Cloudflare Pages rejects files over 25 MB, which rules out WAV for anything longer than a couple of minutes.
- The three files in `tracks/` now are test tones. Delete them when you add real tracks.

Commit, push, and the site redeploys.

## Running locally

```
npm install
npm run dev        # http://localhost:5173, also reachable from your phone on the same Wi‑Fi
npm run build      # production build in dist/
npm run preview    # serve dist/ with the service worker active
```

The service worker (offline support, update prompt) is only active in `build` / `preview`.

## Deploying to Cloudflare Pages

1. Push the repo to GitHub.
2. In Cloudflare, create a Pages project connected to the repo.
3. Build command: `npm run build`. Build output directory: `dist`. Node version is read from `.node-version`.

Every push to the default branch deploys. Cache headers for hashed assets and the service worker live in `public/_headers`.

## Setting up the device for a gig

1. Open the site in Safari, then Share → **Add to Home Screen**. Launch it from the Home Screen icon from now on. This gives a full‑screen app with its own persistent storage.
2. Tap **Download N tracks** at the top while on Wi‑Fi. When it reads **Offline · xx MB**, every track is stored on the device and the app works with no connection.
3. Tap any song once. The status chip switches to **Awake**, which means the screen will not lock while the app is in front. If it says **Can’t keep awake**, set Settings → Display & Brightness → Auto‑Lock → Never for the gig.
4. Optional: Settings → Accessibility → Guided Access lets you lock the device to this app so a stray swipe cannot leave it.
5. Plug into the console with a USB‑C (or Lightning) audio adapter or interface. Audio plays regardless of the ring/silent switch.

When a new version has been deployed, an **Update available** pill appears. Tap **Reload** between songs, not during one.

## How it works

- `src/tracks.ts` discovers audio files at build time with `import.meta.glob`, so there is no manifest to maintain.
- `src/player.ts` wraps a single `<audio>` element and enforces the one‑at‑a‑time rules. Playback starts synchronously inside the tap handler, which iOS requires.
- `src/wakelock.ts` uses the Screen Wake Lock API and re‑acquires the lock whenever the app returns to the foreground.
- `src/offline.ts` downloads tracks into the Cache Storage API. `src/sw.ts` (Workbox) precaches the app shell and serves cached tracks, slicing them to satisfy the byte‑range requests Safari makes for audio.
- `src/ui.ts` renders the set tabs, song list, and now‑playing bar. Mobile first; two columns on wider screens.

Icons are in `public/icons/`. To regenerate the PNGs from the SVGs with ImageMagick:

```
cd public/icons
magick -background none -density 384 favicon.svg -resize 512x512 icon-512.png
magick -background none -density 384 favicon.svg -resize 192x192 icon-192.png
magick -background none -density 384 icon-maskable.svg -resize 512x512 icon-512-maskable.png
magick -background '#0b0b0d' -density 384 icon-maskable.svg -resize 180x180 apple-touch-icon.png
```
