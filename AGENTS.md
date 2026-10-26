# AGENTS.md — Backline

One repo, two halves, one contract between them:

- **Player** (repo root): Vite 8 + vanilla TypeScript page, Cloudflare Worker + Durable Object
  + R2. The leader phone plays into the console; followers see the lyrics live. No framework,
  no unit tests, no lint config; `scripts/browser-check.mjs` is the regression suite.
- **Importer** (`tools/backline/`, Python 3.12, uv): `backline add <song>` → one Reaper project
  (drums as MIDI, lyrics as markers) + song folder. **Once Reaper has saved the project it is the
  source**: `backline export <job>` reads the markers back into the `.lrc`; the user renders the
  `.wav` from it.
- **`tracks/<set>/<NN - Title>/`**: `Title.lrc` (one line per project marker, countdown first),
  `Title.wav` (rendered by the user in Reaper) — on one timeline, count-in first.

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
- `mise run sync-audio` — hash the WAVs (one per song folder), upload missing ones to R2, rewrite `tracks/audio.json`. `--local` targets the wrangler dev bucket. Exits without touching the manifest when no WAVs are present (CI).
- `mise run deploy` — sync audio + build + `wrangler deploy`. Needs `wrangler login`.
- `mise run check:browser` (`node scripts/browser-check.mjs [url]`) — headless Chrome run-through (leader, lyrics, second song, followers, takeover, offline, tablet) against a running preview. Run it after UI or sync changes. It derives its lyric/title expectations from the first song folder in `tracks/`, so it follows the set list; it needs the local R2 filled (`mise run sync-audio -- --local`). After tapping a song, wait for `.now-title` to change (not for something that may already be visible from the previous song) — songs download before they load.
- `mise run test` / `mise run test:slow` / `mise run lint` / `mise run fmt` — the importer (pytest, ruff).
- `backline add <file|url> [--set .. --number ..]`, `backline export [<job>]` (saved project → `.lrc` + `backline.json`, newest `<Title>[-NNN].wav` kept as `<Title>.wav` and the rest removed; warns when the render is older than the project; without a job: every song folder under `tracks/` whose `backline.json` `job` exists — `mise run sync-audio`/`deploy` depend on it), `backline resimplify <job>` (reuses the job's saved options — pass `--count-in`/`--grid`/`--bpm` explicitly to change an existing job; config defaults only apply to new ones; a Reaper-saved project is kept and only the MIDI in `output/` is refreshed unless `--overwrite`), `backline lyrics <job>` (new lookup; a Reaper-saved project keeps its markers unless `--replace`, and a miss always leaves them), `backline rename <job> --title/--artist` (job dir, project, song folder + files incl. the render, then reruns output), `backline fx-template <RPP>`, `backline doctor`.

The reference material: `https://www.youtube.com/watch?v=flr8vDQ9Wuc` (Juanes – Fotografía; music
video 244 s vs 241 s album cut → lyrics offset +0.80 s is expected), job
`~/Music/Backline/jobs/Juanes - Fotografia (4b74e33d)`, song folder `tracks/Cumple de Guille/02 - Fotografía`.

## Tracks

- One folder per song: `tracks/<set>/<NN - Title>/` with one `.wav` (git-ignored) and optionally one `.lrc`/`.txt` (committed). Track id = folder path, used in the realtime protocol; renaming a folder changes the id.
- `tracks/audio.json` entries: `{ key, bytes, source, uploaded }`. `src/tracks.ts` reads it for audio (`/audio/<hash>.wav`) and globs lyrics with `import.meta.glob(..., { query: '?raw' })`. A song needs an audio entry to be listed; lyrics without audio only warn.
- The `.lrc` is on the count-in timeline (countdown lines first) and is **generated from the project's markers** by `backline export`. Never hand-edit it; edit the markers in Reaper and export.
- `backline.json` (committed) is the song's provenance: source hash + URL/file + yt-dlp meta, job path, timeline, `lyrics.source` (`project` once Reaper owns it, else the lookup). `catalog.find_by_hash()` dedupes `backline add` on it (an existing folder wins over `--set/--number`); `Job.find()` dedupes the job folder by the same hash. Renumbering = renaming the folder; the record moves with it.
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
- `main.ts` mounts `UI` (leader) or `FollowUI` (follower) into `#app` and swaps them at runtime; `Player` and the `<audio>` element are created once. There is deliberately **no second audio element on the leader** (a reference track and a baked metronome were tried and dropped: two 60 MB streams in one browser did not stay in lockstep, and the click in the gaps was not reliable). What the leader plays is exactly the Reaper render.
- **The leader plays only from the offline cache.** `main.ts` `selectTrack()` runs `ensureCached()` (per-file download with byte progress, never evicts) then `cachedBlobUrl()` and hands the `blob:` URL to `Player.select(track, src)`; the previous blob URL is revoked. Streaming 60 MB WAVs stutters, and a blob keeps the service worker's range slicing of cached responses out of the audio path. Rows show `.song-dl` ("Not downloaded" / "Downloading 42 %") via `ui.updateDownloads()`/`updateDownloading()`. Followers (monitor) still stream through the SW.
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
  cli.py        typer: add / export / resimplify / lyrics / rename / fx-template / doctor. Rich to stderr.
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
  tempo.py      Beat This! (raw → work/beats.json) and the grid (work/grid.json, what everything
                downstream uses): regularize() (quarter note within 60–170 bpm, drop double-time
                extras in phase, fill ≥1.7× gaps, keep slow-downs; downbeats that imply an absurd
                tempo are ignored — they flip with the beats), then choose_grid(): straight-line
                fit on confident beats (drums active + regular) → straight grid when p95 ≤ 35 ms
                and max ≤ 60 ms, else smooth() (local quadratic) of the tracked grid; --bpm forces
                straight. Beat This! times sit on 10 ms frames, so ±10 ms residual is its noise
                floor and the median interval is coarse (120.00 was really 117.68).
  reaper.py     project (stem muted L, MIDI centred + FX template, daring muted, original muted),
                RENDER_* settings; markers: marker_lines() / read_markers() (plain markers only,
                flags & 1 == 0; regions skipped; Reaper's "…" / '…' / `…` quoting) /
                replace_markers() (swaps the plain markers of a saved project, nothing else);
                extract_fx_chain(); is_generated() (header check: ours vs saved by Reaper)
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
  `write_midi()`, `reaper.py` and the initial markers all derive from the same `Layout`, built
  from `grid.json`. Never compute timing in a renderer. `beats.json` stays the raw detection;
  `GRID_VERSION` in the tempo stage params must be bumped when `choose_grid()` changes (the stage
  is cached on it).
- **The project is the source once Reaper has saved it** (`is_generated()` header check). The
  output stage then keeps it (`RunResult.project_kept`) and only `backline lyrics --replace`
  (`_Runner(replace_lyrics=True)`) touches it, swapping the plain markers; `--overwrite`
  regenerates it wholesale. **Default must be non-destructive**: the first `backline lyrics`
  swapped markers by default and wiped a user's hand-added `(INTRO)` marker within an hour of
  shipping (recovered from Reaper's `Backups/`). The `.lrc` is **always** `read_markers()` of
  whatever project is there (countdown markers included; a project without lyric markers → no
  `.lrc`), so there is one code path for `add`, `resimplify`, `lyrics`, `rename` and `export`
  (= a rerun of the output stage). The `.lrc` is not a cached artifact (it is conditional).
  Reaper does not notice external edits: `backline lyrics --replace` must run with the project
  closed. Reaper saves RPPs with **CRLF** and writes hand-inserted markers as bare names with
  flags `8` (`MARKER 55 5.647 (INTRO) 8 0 1 B {GUID} 0 2`): read/write the project with
  `newline=""` (`Path.read_text()` normalises CRLF away) and treat any `flags & 1 == 0` as a
  marker. `_tidy_renders()` runs in the output stage: of `<stem>.wav` + `<stem>-NNN.wav` the
  newest by mtime wins and is renamed to `<stem>.wav`.
- **Song time vs output time.** `notes.json`, `beats.json`, `lyrics.json` are in song time; the
  output stage adds `Layout.shift`. The count-in ends exactly on the first downbeat.
- **Lyrics never fail the run**: a miss is written to `lyrics.json` (`found: false`) and cached.
  Offsets ≥ 0.5 s against the vocal stem are applied; smaller ones only reported (LRC lines
  are tagged a touch early by convention). `estimate_offset()` is two-stage: the fine ±6 s
  onset-contrast search, plus `_gross_shift()` over ±60 s on the sung/silent *pattern*
  (line live until the next one, ≤ 6 s, vs activity > 0.35) — only when it beats the best
  pattern score inside ±6 s by ≥ 0.08 does it move the fine window, which then picks by
  agreement first and contrast second (a chorus every 8 bars gives several shifts with good
  onsets, one with every line sung). Onset contrast alone picked −50.7 s for *The Look* where
  −23.3 s is right; the 33 s instrumental break is what decides it.
- The FX template `~/.config/backline/midi-track.RfxChain` is injected on both MIDI tracks with
  fresh `FXID`s; it is personal and lives outside the repo.
- Heavy imports (torch, demucs, beat_this) stay inside functions.

Dependency gotchas (already handled): `[tool.uv] environments` excludes Intel macOS or uv's
universal lock downgrades torch/numpy for everyone; `adtof-pytorch` is a git dep pinned by
commit, prints on load (wrapped), wants a `[1, T, 5]` batch, CPU only; torchaudio is frozen at
2.11 while torch moves on, so audio I/O goes through soundfile/ffmpeg; LRCLIB 503s sporadically
(retries); `HF_HUB_VERBOSITY=error` is set in `cli.main()` because huggingface_hub resets its
logger on lazy import; MIDI lyric events are UTF-8.

Reaper format (from projects saved by Reaper 7, locked by tests): `VOLPAN <vol> <pan> -1 -1 1`,
`MUTESOLO <mute> 0 0`, in-project MIDI as `HASDATA 1 480 QN` + `E <delta> <status> <d1> <d2>` +
final `E .. b0 7b 00`, `MARKER <n> <pos> "<text>" 0` (Reaper saves it back as `MARKER <n> <pos> "<text>" 0 0 1 B {GUID} 0 2`; regions have flags 1 and a closing line with an empty name), `RENDER_FILE/RENDER_PATTERN/RENDER_FMT 0 2
44100/RENDER_RANGE 1 0 0 0 1000`, `<RENDER_CFG>` `ZXZhdxgAAQ==` = 24-bit WAV.

Conventions: ruff (line length 100, E/F/I/UP/B; B008 ignored in cli.py), dataclasses for data,
functions for stages, comments only where behaviour is not obvious. Commit after each working
increment; imperative subject, body says why.

## Status / next

Done: merged repo; `backline add` with config, song folders, drums-only project with lyric
markers; the Reaper project as the source (`export`, marker swap in `lyrics`); leader plays
only downloaded tracks from blob URLs. Dropped after trying them live: the reference track in
the app and the metronome baked into the render (see the Roles section). Reaper-saved projects
predating countdown markers (`Ironic`, `No Podras`) have no countdown lines until
`backline lyrics` or `--overwrite` is run on them.
Open (see `docs/PLAN.md`): per-beat tempo map, quantized `essence`/`detailed` styles,
`--downbeat-offset`, Whisper fallback for lyrics, `--json` progress, a `--render` flag driving
Reaper's command-line renderer.
Player: `tracks.ts` globs `backline.json` too; `Track.artist` and `Track.source` feed the list
(`.song-artist`), the now-playing meta (`describe()` = "Artist · Set", `.now-source` link with
`target=_blank rel=noopener`) and the media-session metadata.
