# Backline

Everything a band needs behind it. Two halves in one repo:

- **The player** — a web app on one phone (the *leader*, plugged into the console) plays the
  backing track; everyone else opens the same address and follows the lyrics live. Works
  offline once the tracks are downloaded.
- **The importer** — `backline add <song>` turns an audio file or a YouTube link into a song
  folder for the player: timed lyrics, a reference track, and a Reaper project that renders
  the drums.

```bash
backline add "https://www.youtube.com/watch?v=flr8vDQ9Wuc"
# open the Reaper project it prints, File → Render
mise run sync-audio
mise run deploy
```

Each song folder holds three files on **one timeline** — two bars of count-in, then the song:

```
tracks/Cumple de Guille/02 - Fotografía/
├── Fotografia.lrc             lyrics; the count-in shows as a countdown, 1 2 3 4 · 1 2 3 4
├── Fotografia.wav             the drums (your drum instrument, rendered from Reaper)
└── Fotografia.reference.wav   the original song, with the same count-in clicks
```

The band plays to the drums. The leader can bring the original in underneath at any volume,
or mute it, without anything going out of sync.

## Setup

[mise](https://mise.jdx.dev) installs everything: Node 22, Python 3.12, uv, ffmpeg, yt-dlp,
and the JS and Python dependencies.

```bash
git clone … backline && cd backline
mise install
backline doctor         # the importer's tools, packages and GPU
```

Model weights (~80 MB Demucs, ~78 MB Beat This!) download on the first `backline add`.

## Adding a song

```bash
backline add <audio-file | URL> [--set "Cumple de Guille"] [--number 3]
```

1. **Analysis** (~20 s on an M-series Mac): Demucs separates drums and vocals, ADTOF
   transcribes the drums, Beat This! finds the beat and the first downbeat, lyrics come from
   [LRCLIB](https://lrclib.net) and are checked against the isolated vocal.
2. **The song folder** `tracks/<set>/<NN - Title>/` gets `Title.lrc` and
   `Title.reference.wav` right away. The set comes from `--set` or `backline.toml`; the number
   from `--number` or the next free one. Running the same song again reuses its folder.
3. **The drums** need your instrument, so they go through Reaper: open the project the command
   prints (in `~/Music/Backline/jobs/<Artist - Title>/`), File → Render. The dialog is
   pre-filled to put `Title.wav` in the song folder. Your drum VSTi is already on the track
   (see *Your drum instrument*).
4. `mise run sync-audio` uploads the two WAVs to R2 and updates `tracks/audio.json`;
   `mise run deploy` publishes.

### Options

```
--set NAME            set (tab) to add the song to; default from backline.toml
--number N            position in the set; default: next free
--to DIR              an explicit song folder instead of tracks/<set>/<NN - Title>
--artist / --title    override what was read from tags, the YouTube title, or the file name
--lyrics-file FILE    use your own .lrc/.txt instead of looking lyrics up
--lyrics-offset SEC   shift the lyrics by hand (skips the automatic check)
--no-lyrics
--count-in BARS       default 2 (backline.toml); 0 = none
--quality fast|best   Demucs htdemucs (default) or htdemucs_ft (~4× slower)
--force               ignore the cache and rerun every stage

backline resimplify <job> [--count-in 0 …]   regenerate the outputs from the cached analysis
backline lyrics <job> [--artist … --title … --offset …]   redo only the lyrics
backline fx-template <Title.RPP>              remember your drum VSTi
backline doctor
```

Inputs: wav, mp3, m4a, aac, flac, aiff, ogg, opus, or any URL `yt-dlp` understands.

### The job folder

Everything that is not a deliverable lives outside the repo, in `~/Music/Backline/jobs/`:

```
Juanes - Fotografia (4b74e33d)/
├── Fotografia.RPP         the Reaper project
├── input/                 source.webm (or your file), source.json, original.wav
├── work/                  stems, activations, beats, notes, lyrics.json — disposable
└── output/                Fotografia.mid, Fotografia (daring).mid
```

Every stage is cached there, so re-running is instant and `resimplify`/`lyrics` only redo
what changed.

### The Reaper project

| Track | Content | Pan | State |
|---|---|---|---|
| `<Title> (drums)` | the real drums (Demucs stem) | hard left | muted — unmute to compare |
| `<Title> (MIDI conservative)` | the drums as MIDI | centre | on, your VSTi loaded |
| `<Title> (MIDI daring)` | same, more sensitive detection | hard right | muted, your VSTi loaded |
| `<Title> (original)` | the song | right | muted — the app mixes the reference in live |

Every lyric line is a marker on the ruler. File → Render is pre-filled: the song folder,
`<Title>.wav`, 24-bit 44.1 kHz, whole project — so the render is drums only, count-in
included. **Once Reaper has saved the project, Backline never writes to it again**; a
regeneration goes to `<Title>.new.RPP` beside it.

*Conservative vs daring:* the conservative MIDI uses the transcriber's default thresholds and
rarely invents hits but is shy on toms and cymbals; the daring one lowers the thresholds and
catches more at the cost of some false positives, which come in quiet because velocity follows
confidence. GM notes: kick 36, snare 38, toms 45, hi-hat 42, cymbals 49, count-in 37.

### Your drum instrument

Once: open a generated project, put your drum VSTi on the MIDI track, pick the kit, save, then

```bash
backline fx-template "~/Music/Backline/jobs/<job>/<Title>.RPP"
```

The chain (plugin and state, kit included) is stored in `~/.config/backline/midi-track.RfxChain`
and loaded on both MIDI tracks of every project from then on.

### Lyrics

The song is identified from `--artist/--title`, the file's tags, the YouTube title and
channel, or the file name, and looked up on LRCLIB (open, no account). Candidates are ranked by
name similarity, duration and whether they are time-synced. The result is checked against the
isolated vocal: a gross mismatch (a music video with a longer intro than the album cut) is
corrected with a constant shift; small disagreements are left alone because LRC lines are
conventionally tagged a touch early. `work/lyrics.json` records what was considered and why.
Studio recordings only — a live or cover version gets the right words at the wrong times.

### `backline.toml`

```toml
[paths]
jobs = "~/Music/Backline/jobs"   # input/, work/ and the project; big, not in git
tracks = "tracks"                # the song folders, relative to this file

[tracks]
default_set = "Cumple de Guille"

[count_in]
bars = 2
note = 37
```

## Using the player

Everyone opens the same URL and starts as a follower.

### Leader

1. Tap **Lead** at the top and enter the leader key. The device switches to the player.
2. **Tap a song** to load it. Nothing plays yet; the transport appears at the bottom and, if the song has lyrics, they open. **‹ Songs** goes back to the list (on an iPad the list and the lyrics sit side by side).
3. **Play** starts the track, or resumes from where you paused. **Pause** keeps the position. **Restart** goes back to the top; if it was playing it keeps playing.
4. To jump somewhere, **tap a lyric line** and playback moves to it, or **tap or drag the progress bar** (the time shows where you'll land; it seeks when you let go). Followers jump along. Scrolling the lyrics never seeks, and the list stops auto-scrolling for a few seconds while you read ahead.
5. **Reference.** When a song has one, a row appears above the transport: tap **Reference off** to bring the original song in under the drums, set its level with the slider, **Mute** to drop it without losing sync. Off by default; the choice and the level are remembered on the device. Nothing is downloaded until it is turned on.
6. Loading another song unloads the current one. When a track ends it rewinds and waits.

The chips at the top show status: **Offline · xx MB** (all tracks stored on the device) or **Download N tracks** (tap to store them; references are included when Reference is on), **Awake** (screen won't lock), and **Leader · N** (connected, N followers; it reads **Leader · offline** when the room can't be reached). Tap **Leader** to step down.

There is only one leader. If a second device enters the key, the first one drops back to following and says so. The leader keeps playing with no internet; followers keep running on the last position they heard and catch up as soon as it is back.

### Followers

Open the address, that's it. The current song and the highlighted line follow the leader. **Live** means connected; **Leader offline** means the leader lost its connection and the lyrics keep running on the last known position. Tap once so the screen stays awake.

Tap **Muted** to hear the drums track on this device too. It starts, pauses, and restarts with the leader and stays within a fraction of a second of it; there is no transport, the leader drives. Tap **Listening** to mute again. Nothing is downloaded while muted; listening streams the full WAV (about 10 MB per minute), which is worth knowing on cellular. If the chip says **Tap to listen**, the browser refused to start audio on its own; tapping it again fixes that.

### Leader key

The default key is `goodkarma`, set in `wrangler.jsonc`. To change it without committing anything:

```
npx wrangler secret put LEADER_KEY
```

Devices holding the old key are refused on their next connection and go back to following.

## Setting up the devices for a gig

Leader (iPhone or iPad on the console):

1. Open the site in Safari, then Share → **Add to Home Screen**. Launch it from the icon from now on.
2. Tap **Lead** and enter the key.
3. Turn **Reference** on first if you want it offline, then tap **Download N tracks** while on Wi-Fi. When it reads **Offline · xx MB**, every track is on the device and playback works with no connection. After new songs are deployed, the chip shows how many are missing; tap it again.
4. Tap any song once. The chip switches to **Awake**. If it says **Can't keep awake**, tap once more; if it still does, set Settings → Display & Brightness → Auto-Lock → Never for the gig.
5. Optional: Settings → Accessibility → Guided Access locks the device to this app.
6. Plug into the console with a USB-C (or Lightning) audio adapter or interface. Audio plays regardless of the ring/silent switch.

Followers: open the address, tap once. Following needs internet (venue Wi-Fi or cellular); playback on the leader does not.

When a new version has been deployed, an **Update available** pill appears. Tap **Reload** between songs, not during one.

## Tracks

One folder per song inside `tracks/`, grouped in set folders. `backline add` creates them, but
any folder with the right files works:

```
tracks/
└── Cumple de Guille/
    ├── 01 - No podrás/
    │   ├── NoPodras.wav
    │   └── NoPodras.lrc
    └── 02 - Fotografía/
        ├── Fotografia.wav
        ├── Fotografia.reference.wav
        └── Fotografia.lrc
```

- A leading number controls the order and is stripped from the name shown. The folder name is the song title.
- Set folders become tabs. With a single set the tabs are hidden.
- Each song folder holds one `.wav` (what the band plays to), optionally one `.reference.wav` (the original, same timeline), and optionally one `.lrc`/`.txt`. Other files are ignored.
- `.lrc` lyrics are timed (`[01:02.50]line`) and get the highlighted, auto-scrolling view. Untimed lines (metadata, credits) are ignored. A `.txt` is shown as plain scrollable text.

The WAV files are not committed; `mise run sync-audio` hashes them, uploads the missing ones to
R2 and rewrites `tracks/audio.json`, which is committed so a build anywhere knows every track.

## Running locally

Two processes: Vite for the page, Wrangler for the Worker (audio + realtime).

```
mise run sync-audio -- --local   # put the WAVs into the local R2 used by wrangler dev
mise run dev:worker              # worker on http://localhost:8787
mise run dev                     # page on http://localhost:5173, proxies /audio and /ws to the worker
```

Other commands:

```
mise run typecheck    # wrangler types + tsc for the page and the worker
mise run build        # typecheck + production build in dist/
mise run preview      # build, then serve dist/ through the worker with the service worker active
mise run deploy       # sync audio + build + publish to Cloudflare
mise run check:browser   # headless Chrome run-through against a running preview
mise run test         # the importer's fast tests; test:slow runs the models end to end
mise run lint         # ruff
```

The service worker (offline support, update prompt) is only active in `preview`, not in `dev`. To try leader and follower locally, use two browser profiles (or a private window), since the role is stored per browser.

## Deploying to Cloudflare

The app is a Cloudflare Worker with static assets (`wrangler.jsonc`): the Worker streams `/audio/*` from the R2 bucket, routes `/ws` to the `Room` Durable Object, and serves everything else from `dist/`. The worker is still named `band-companion` and the bucket `band-companion-audio` — that is the deployed URL and the uploaded audio, so they stay.

First time:

```
npx wrangler login
npx wrangler r2 bucket create band-companion-audio
mise run deploy
```

After that `mise run deploy` is all that's needed. A git-connected build in the Cloudflare dashboard also works (build command `npm run build`, deploy command `npx wrangler deploy`) because `tracks/audio.json` is committed; just run `mise run sync-audio` locally whenever audio changes.

Cache headers for hashed assets and the service worker live in `public/_headers`; audio headers are set by the Worker.

## Architecture

```mermaid
flowchart TB
    subgraph import["backline add (your Mac)"]
        direction LR
        SRC["file / YouTube"]
        ML["Demucs · ADTOF · Beat This! · LRCLIB"]
        RPP["Reaper project<br/>(you render the drums)"]
        SRC --> ML --> RPP
    end

    subgraph tracks["tracks/‹set›/‹NN - Title›/"]
        direction LR
        LRC["Title.lrc"]
        WAV["Title.wav (drums)"]
        REF["Title.reference.wav"]
    end

    subgraph leader["Leader device (on the console)"]
        direction LR
        LUI["Player UI<br/>song list · transport · lyrics · reference"]
        LA["audio element"]
        LR["reference audio<br/>→ gain node"]
        LB["Broadcaster"]
        LUI --> LA
        LUI --> LR
        LA -- "position" --> LB
    end

    subgraph cf["Cloudflare Worker"]
        direction LR
        DO[("Room<br/>Durable Object")]
        W["worker/index.ts"]
        R2[("R2 bucket")]
        AS["Static assets<br/>dist/"]
        W -- "/ws" --> DO
        W -- "/audio/‹hash›.wav" --> R2
        W -- "everything else" --> AS
    end

    subgraph followers["Follower phones"]
        direction LR
        FUI["Lyrics view"]
        FF["Follower"]
        FM["Monitor audio<br/>(optional)"]
        FF --> FUI
        FF --> FM
    end

    ML --> LRC
    ML --> REF
    RPP --> WAV
    tracks -- "mise run sync-audio" --> R2
    LB -- "WebSocket: state" --> W
    LA -- "audio, byte ranges" --> W
    LR -- "audio, byte ranges" --> W
    W -- "WebSocket: state" --> FF
    W -- "audio, byte ranges" --> FM
```

### Realtime flow

The room keeps the last state, so late joiners are in sync immediately. Followers do not stream a position; they receive it on every change and every two seconds while playing, and run their own clock in between.

```mermaid
sequenceDiagram
    participant L as Leader
    participant R as Room (Durable Object)
    participant F as Follower

    L->>R: connect /ws?role=leader&key=…
    R-->>L: welcome · followers count
    Note over R: wrong key → close 4001<br/>another leader → previous one closed 4002

    F->>R: connect /ws?role=follower
    R-->>F: welcome · last state

    loop on play / pause / seek, and every 2 s while playing
        L->>R: state { track, playing, position }
        R-->>F: state
        Note over F: position += time since received<br/>lyrics (and optional audio) follow
    end
```

## How it works

**Importer** (`tools/backline/`, Python):

- `ingest.py` — ffmpeg, or `yt-dlp` for URLs → `input/original.wav`.
- `separate.py` — Demucs `htdemucs` on the GPU → drum and vocal stems.
- `transcribe.py` — ADTOF: five drum classes at 100 frames/s. `simplify.py` picks peaks with two threshold profiles.
- `tempo.py` — Beat This!: beats, downbeats, BPM.
- `lyrics.py` / `lrclib.py` — identification, lookup, ranking, and the vocal-stem timing check.
- `midi_out.py` — one `Sequence` (count-in, shift, note ticks) feeds both the `.mid` writer and the Reaper project, so they can never disagree.
- `reference.py` — the original on the count-in timeline, clicks synthesized.
- `reaper.py` — the practice-mix project, your FX chain, render settings, lyric markers.
- `pipeline.py` — stages with per-job caching; `config.py` — `backline.toml` and song folders.

**Player** (`src/`, TypeScript):

- `scripts/sync-audio.mjs` hashes the WAVs (main and reference), uploads them to R2 and writes `tracks/audio.json`.
- `src/tracks.ts` builds the song list from `audio.json` plus the `.lrc`/`.txt` files found with `import.meta.glob`. Audio URLs are `/audio/<hash>.wav`.
- `worker/index.ts` streams audio from R2 with byte-range support (Safari needs it) and hands `/ws` to `worker/room.ts`, a Durable Object holding one room: the leader's socket pushes `{ track, playing, position }` on every change and every two seconds while playing; follower sockets receive it, and late joiners get the last state.
- `src/sync.ts` is the client side: `Broadcaster` for the leader, `Follower` for everyone else. Followers run the position on their own clock between updates, which is plenty for lyric lines. Both reconnect with backoff and when the app returns to the foreground.
- `src/player.ts` wraps the main `<audio>` element. Playback starts synchronously inside the tap handler, which iOS requires.
- `src/reference.ts` is the leader's optional second `<audio>`, locked to the main one on play, pause, seek and drift, and routed through a Web Audio gain node — iOS ignores `audio.volume`, a gain node it respects. Built inside the tap that turns it on.
- `src/lyrics.ts` parses LRC; `src/lyrics-view.ts` highlights and scrolls the current line every frame.
- `src/monitor.ts` is the follower's optional audio: a second `<audio>` element that loads the leader's track and seeks whenever it drifts more than half a second from the extrapolated position.
- `src/ui.ts` is the leader screen, `src/follow-ui.ts` the follower screen; `src/main.ts` mounts one or the other depending on whether the device holds a key (`src/role.ts`).
- `src/offline.ts` downloads tracks (and references, when enabled) into the Cache Storage API; `src/sw.ts` (Workbox) precaches the app shell and serves cached audio, slicing it for byte-range requests.
- `src/wakelock.ts` uses the Screen Wake Lock API and re-acquires the lock when the app returns to the foreground.

Icons are in `public/icons/`. To regenerate the PNGs from the SVGs with ImageMagick:

```
cd public/icons
magick -background none -density 384 favicon.svg -resize 512x512 icon-512.png
magick -background none -density 384 favicon.svg -resize 192x192 icon-192.png
magick -background none -density 384 icon-maskable.svg -resize 512x512 icon-512-maskable.png
magick -background '#0b0b0d' -density 384 icon-maskable.svg -resize 180x180 apple-touch-icon.png
```

## Limitations

- Five drum classes: all toms are one note, crash and ride are one note, no open/closed hi-hat.
- Constant tempo in the MIDI and the project; a per-beat tempo map is planned.
- Drum notes are not quantized yet; grid-snapped styles are planned.
- Lyrics need the song to exist on LRCLIB; originals come back "not found" (a Whisper fallback is planned).
- `yt-dlp` breaks when YouTube changes; `mise upgrade yt-dlp` fixes it.

See `docs/PLAN.md` for the importer's design and roadmap, `AGENTS.md` for working conventions.
