# AGENTS.md — Backline

One repo, two halves, one contract between them:

- **Player** (repo root): Vite 8 + vanilla TypeScript page, Cloudflare Worker + Durable Object
  + R2. The leader phone plays into the console; followers see the lyrics live. No framework,
  no unit tests, no lint config; `scripts/browser-check.mjs` is the regression suite.
- **Importer** (`tools/backline/`, Python 3.12, uv): `backline add <song>` → song folder.
- **`tracks/<set>/<NN - Title>/`**: `Title.lrc`, `Title.wav` (L metronome / R drums, rendered by
  the user in Reaper), `Title.reference.wav` (the original) — all on one timeline, count-in first.

The name was Band Companion; the deployed worker (`wrangler.jsonc` `name`), the R2 bucket and
the leader-key localStorage key keep that name on purpose (URL, uploaded audio, signed-in
leaders). Everything else says Backline. `README.md` is accurate; read it for the workflow.

## Commands

`mise install` sets up both halves (node + python + uv + ffmpeg + yt-dlp, then `npm ci && uv sync`).
The venv is activated on `cd`; `backline` is on PATH. `UV_PROJECT` points uv at `tools/backline`.

- `mise run dev` — Vite dev server; proxies `/audio` and `/ws` to `mise run dev:worker` (wrangler on 8787). Service worker is NOT active here.
- `mise run dev:worker` — `wrangler dev` with local R2 and a local Durable Object. Fill local R2 with `mise run sync-audio -- --local` first.
- `mise run typecheck` — `wrangler types` (generates the git-ignored `worker-configuration.d.ts`), then `tsc` for the page (`tsconfig.json`: `src`, `shared`) and the worker (`worker/tsconfig.json`). Run after any TS change.
- `mise run build` — typecheck + `vite build`. `mise run preview` builds and serves `dist/` through the worker with the SW active; use it for caching, range requests, offline.
- `mise run sync-audio` — hash WAVs (main and `.reference.wav`), upload missing ones to R2, rewrite `tracks/audio.json`. `--local` targets the wrangler dev bucket. Exits without touching the manifest when no WAVs are present (CI).
- `mise run deploy` — sync audio + build + `wrangler deploy`. Needs `wrangler login`.
- `mise run check:browser` (`node scripts/browser-check.mjs [url]`) — headless Chrome run-through (leader, lyrics, reference, followers, takeover, offline, tablet) against a running preview. Run it after UI or sync changes. It derives its lyric/title expectations from the first song folder in `tracks/`, so it follows the set list; it needs the local R2 filled (`mise run sync-audio -- --local`).
- `mise run test` / `mise run test:slow` / `mise run lint` / `mise run fmt` — the importer (pytest, ruff).
- `backline add <file|url> [--set .. --number ..]`, `backline resimplify <job>` (reuses the job's saved options — pass `--metronome`/`--count-in`/`--grid`/`--bpm` explicitly to change an existing job; config defaults only apply to new ones), `backline lyrics <job>`, `backline rename <job> --title/--artist` (job dir, project, song folder + files incl. the render, then reruns output), `backline fx-template <RPP>`, `backline doctor`.

The reference material: `https://www.youtube.com/watch?v=flr8vDQ9Wuc` (Juanes – Fotografía; music
video 244 s vs 241 s album cut → lyrics offset +0.80 s is expected), job
`~/Music/Backline/jobs/Juanes - Fotografia (4b74e33d)`, song folder `tracks/Cumple de Guille/02 - Fotografía`.

## Tracks

- One folder per song: `tracks/<set>/<NN - Title>/` with one `.wav` (git-ignored), optionally one `*.reference.wav` (git-ignored; the original on the same timeline) and optionally one `.lrc`/`.txt` (committed). Track id = folder path, used in the realtime protocol; renaming a folder changes the id.
- `tracks/audio.json` entries: `{ key, bytes, source, uploaded, reference?: { key, bytes, source, uploaded } }`. `src/tracks.ts` reads it for audio (`/audio/<hash>.wav`) and globs lyrics with `import.meta.glob(..., { query: '?raw' })`. A song needs an audio entry to be listed; lyrics without audio only warn.
- The `.lrc` is on the count-in timeline (countdown lines first). Never hand-edit timings to match a different render; regenerate with `backline resimplify`.
- `backline.json` (committed) is the song's provenance: source hash + URL/file + yt-dlp meta, job path, timeline, lyrics source. `catalog.find_by_hash()` dedupes `backline add` on it (an existing folder wins over `--set/--number`); `Job.find()` dedupes the job folder by the same hash. Renumbering = renaming the folder; the record moves with it.
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
- `src/reference.ts` (leader's reference track) uses `<audio id="reference">`, locked to the main element's own `play`/`pause`/`seeking`/`timeupdate` events — gaps > 300 ms (and the leader's own seeks) are closed with a seek, followed by exactly one corrective seek 2 s later if the slower-buffering element left > 80 ms behind; after that only a fixed ±1.5 % `playbackRate` nudge with hysteresis (engage > 60 ms, release < 10 ms) on an EMA-smoothed drift. Both corrections are audible when overused — seeks re-buffer, and Firefox/WebKit time-stretchers crackle on every rate change — so in steady state the controller must do nothing; verify with a probe counting `seeking`/`ratechange` events, not by eye. Audio is routed through an `AudioContext` → `MediaElementAudioSourceNode` → `GainNode`, because iOS ignores `audio.volume`. The graph is built once, inside the tap that turns the reference on (a MediaElementSource can only ever be attached once, and iOS needs the gesture); `main.ts` therefore creates one `Reference` for the page's lifetime. Mute = gain 0 with the element still playing, so unmute is instant. State (`enabled`, `muted`, `volume`) is in localStorage `backline:reference`. Off → nothing loaded, nothing downloaded; `offline.ts` includes references only when enabled.
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

## Importer (`tools/backline/`)

```
src/backline/
  cli.py        typer: add / resimplify / lyrics / rename / fx-template / doctor. Rich to stderr.
  config.py     backline.toml (found upwards from cwd, else repo root); song_folder() numbering
  catalog.py    backline.json per song folder: write_record(), find_by_hash() for dedupe
  pipeline.py   stages + per-job caching (_Runner), RunOptions/RunResult, output-folder resolution
  job.py        job layout (input/ work/ output/ + <Title>.RPP), path constants, manifest
  ingest.py     file or URL → input/original.wav (ffmpeg; yt-dlp subprocess for URLs)
  separate.py   Demucs → work/drums.wav + work/vocals.wav (MPS, CPU fallback)
  transcribe.py ADTOF → activations [frames, 5] @ 100 fps
  tempo.py      Beat This! → beats/downbeats/bpm/meter
  simplify.py   peaks → notes; PROFILES (conservative / daring thresholds)
  lyrics.py / lrclib.py   identification, LRCLIB lookup + ranking, vocal-stem offset check, LRC I/O
  midi_out.py   CountIn, TempoMap, Layout, build_sequence() → Sequence; write_midi()
  reference.py  original + synthesized count-in clicks on the output timeline, 16-bit
  metronome.py  write_metronome(): one plain click per quarter note on the regularized grid
                (modes always/gaps/off) → work/metronome.wav, mono; detect_gaps() for gaps mode
  tempo.py      Beat This! (raw → work/beats.json) and the grid (work/grid.json, what everything
                downstream uses): regularize() (quarter note within 60–170 bpm, drop double-time
                extras in phase, fill ≥1.7× gaps, keep slow-downs; downbeats that imply an absurd
                tempo are ignored — they flip with the beats), then choose_grid(): straight-line
                fit on confident beats (drums active + regular) → straight grid when p95 ≤ 35 ms
                and max ≤ 60 ms, else smooth() (local quadratic) of the tracked grid; --bpm forces
                straight. Beat This! times sit on 10 ms frames, so ±10 ms residual is its noise
                floor and the median interval is coarse (120.00 was really 117.68).
  reaper.py     project (metronome hard L, stem muted L, MIDI hard R + FX template, daring muted R,
                original muted), markers, RENDER_* settings; extract_fx_chain(); is_generated()
tests/          pytest; fixtures/make_loop.py synthesizes a 100 BPM loop; tests pass an explicit
                Config so nothing ever lands in the real tracks/
```

Rules:

- **Job layout is fixed**: `input/`, `work/`, `output/`, `<Title>.RPP` in the root, nothing else.
  Relative paths are constants in `job.py` (`J.ORIGINAL`, `J.DRUMS`, …); never build `"work/..."`
  strings elsewhere. Deliverables go to the song folder: `--to`, else `tracks/<set>/<NN - Title>`
  from `backline.toml` (`song_folder()` reuses an existing folder with the same title,
  accent-insensitively), else the job's `output/`. The resolved folder is saved in the job's
  options so reruns land in the same place. MIDI files stay in the job's `output/`.
- **Stages are cached per job.** Each `stage()` call passes its params and the artifacts it
  produces; once a stage runs, every later stage runs too. New output file → add it to the
  artifact list. Params must be JSON-round-trippable. `OUTPUT_FORMAT` is part of the output
  stage's params: bump it when the output stage changes what it writes for the same inputs.
- **One `Sequence`, every renderer.** `build_sequence()` is the single source of truth for ticks;
  `write_midi()`, `reaper.py`, the `.lrc` files, `reference.py` and `metronome.py` all derive from
  the same `Layout`, built from `grid.json`. Never compute timing in a renderer. `beats.json`
  stays the raw detection; `GRID_VERSION` in the tempo stage params must be bumped when
  `choose_grid()` changes (the stage is cached on it). "Don't be creative" with the click: one
  sound, every quarter note, same level; the user asked for exactly that.
- **The metronome is baked into the render on purpose** (project track hard left, MIDI drums hard
  right): no third element to keep in sync in the player, and L/R can be routed separately. The
  player plays `Title.wav` as-is.
- **Song time vs output time.** `notes.json`, `beats.json`, `lyrics.json` are in song time; the
  output stage adds `Layout.shift`. The count-in ends exactly on the first downbeat.
- **Lyrics never fail the run**: a miss is written to `lyrics.json` (`found: false`) and cached.
  Offsets ≥ 0.5 s against the vocal stem are applied; smaller ones only reported (LRC lines
  are tagged a touch early by convention).
- **Never overwrite a Reaper-saved project** (`project_target()`, header check) — regenerate
  into `<Title>.new.RPP`. The FX template `~/.config/backline/midi-track.RfxChain` is injected
  on both MIDI tracks with fresh `FXID`s; it is personal and lives outside the repo.
- Heavy imports (torch, demucs, beat_this) stay inside functions.

Dependency gotchas (already handled): `[tool.uv] environments` excludes Intel macOS or uv's
universal lock downgrades torch/numpy for everyone; `adtof-pytorch` is a git dep pinned by
commit, prints on load (wrapped), wants a `[1, T, 5]` batch, CPU only; torchaudio is frozen at
2.11 while torch moves on, so audio I/O goes through soundfile/ffmpeg; LRCLIB 503s sporadically
(retries); `HF_HUB_VERBOSITY=error` is set in `cli.main()` because huggingface_hub resets its
logger on lazy import; MIDI lyric events are UTF-8.

Reaper format (from projects saved by Reaper 7, locked by tests): `VOLPAN <vol> <pan> -1 -1 1`,
`MUTESOLO <mute> 0 0`, in-project MIDI as `HASDATA 1 480 QN` + `E <delta> <status> <d1> <d2>` +
final `E .. b0 7b 00`, `MARKER <n> <pos> "<text>" 0`, `RENDER_FILE/RENDER_PATTERN/RENDER_FMT 0 2
44100/RENDER_RANGE 1 0 0 0 1000`, `<RENDER_CFG>` `ZXZhdxgAAQ==` = 24-bit WAV.

Conventions: ruff (line length 100, E/F/I/UP/B; B008 ignored in cli.py), dataclasses for data,
functions for stages, comments only where behaviour is not obvious. Commit after each working
increment; imperative subject, body says why.

## Status / next

Done: merged repo; `backline add` with config, song folders, reference track, drums-only
project; player reference track with live volume/mute; offline includes references when on.
Open (see `docs/PLAN.md`): per-beat tempo map, quantized `essence`/`detailed` styles,
`--bpm`/`--downbeat-offset`, Whisper fallback for lyrics, `--json` progress, followers hearing
the reference, a `--render` flag driving Reaper's command-line renderer.
Player: `tracks.ts` globs `backline.json` too; `Track.artist` and `Track.source` feed the list
(`.song-artist`), the now-playing meta (`describe()` = "Artist · Set", `.now-source` link with
`target=_blank rel=noopener`) and the media-session metadata.
