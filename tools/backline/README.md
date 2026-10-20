# drums2midi

Give it a song — an audio file or a YouTube link — and get a practice package back: the drums
as MIDI with a count-in, the lyrics as a timed `.lrc`, and a Reaper project that is set up to
render a backing track with your drum instrument. Built to feed
[Band Companion](https://github.com/lucasefe/band-companion), where each song is a folder with
one `.wav` and one `.lrc`.

```bash
drums2midi run "https://www.youtube.com/watch?v=flr8vDQ9Wuc" \
    --to "~/Code/band-companion/tracks/Cumple de Guille/02 - Fotografía"
```

```
✓ ingest      ━━━━━━━━━━━━━━━━━━━━━━━━ 2.0s      # yt-dlp
✓ separate    ━━━━━━━━━━━━━━━━━━━━━━━━ 10.7s     # Demucs on the Apple Silicon GPU
✓ transcribe  ━━━━━━━━━━━━━━━━━━━━━━━━ 2.5s      # ADTOF
✓ tempo       ━━━━━━━━━━━━━━━━━━━━━━━━ 1.2s      # Beat This!
✓ simplify    ━━━━━━━━━━━━━━━━━━━━━━━━ 0.0s
✓ lyrics      ━━━━━━━━━━━━━━━━━━━━━━━━ Juanes – Fotografia · 46 lines
✓ output      ━━━━━━━━━━━━━━━━━━━━━━━━ 0.0s
song          Juanes – Fotografia
tempo         85.71 bpm, 4/4, first downbeat at 1.10s
conservative  kick 293  snare 147  tom 1  hihat 553  cymbal 9
daring        kick 295  snare 149  tom 95  hihat 566  cymbal 14
lyrics        Juanes – Fotografia · 46 timed lines · lrclib:33616122 · offset +0.80s applied · on-vocal 100%
count-in      5.60s; the song starts at 4.500s
job           ~/Music/Drums2MIDI/Juanes - Fotografia (4b74e33d)
project       ~/Music/Drums2MIDI/Juanes - Fotografia (4b74e33d)/Fotografia.RPP
output        ~/Code/band-companion/tracks/Cumple de Guille/02 - Fotografía
Next: open the project, File → Render (pre-filled) → Fotografia.wav lands next to Fotografia.lrc
```

About 20 seconds for a 4-minute song on an M-series Mac. Running it again is instant: every
stage is cached in the job folder.

## Setup

Everything comes from [mise](https://mise.jdx.dev): Python 3.12, uv, ffmpeg, yt-dlp, and the
Python dependencies (PyTorch, Demucs, ADTOF, Beat This!).

```bash
git clone … && cd extract-drums
mise install
drums2midi doctor      # every line should be ✓
```

Model weights (~80 MB for Demucs, ~78 MB for Beat This!) download automatically on first run.

## The workflow

1. **Run** with `--to` pointing at the song's folder in Band Companion (it is created if missing).
   The `.lrc` is written there immediately.
2. **Open** `<Title>.RPP` from the job folder. Your drum instrument is already on the MIDI track
   (see *Your drum instrument* below), the mix is set, and File → Render is pre-filled with the
   same song folder, `<Title>.wav`, 24-bit 44.1 kHz, whole project.
3. **Render.** The song folder now holds exactly one `.wav` and one `.lrc`, on the same timeline:
   count-in first, then the song.
4. `cd ~/Code/band-companion && npm run sync-audio`.

Without `--to`, the `.lrc` and the render target are the job's own `output/` folder, which is
laid out the same way — copy it wherever you like.

## Usage

```bash
drums2midi run <audio-file | URL> [options]
    --to DIR              song folder for the .lrc and the render (default: the job's output/)
    --out DIR             root for job folders (default ~/Music/Drums2MIDI)
    --quality fast|best   Demucs htdemucs (default) or htdemucs_ft (~4× slower, a bit cleaner)
    --count-in BARS       bars of side-stick count-in before the first downbeat (default 2; 0 = none)
    --count-in-note N     MIDI note for the click (default 37, GM side stick)
    --artist / --title    override what was read from tags, the YouTube title or the file name
    --lyrics-file FILE    use your own .lrc/.txt instead of looking lyrics up
    --lyrics-offset SEC   shift the lyrics by hand (skips the automatic check)
    --no-lyrics
    --device auto|mps|cpu
    --keep-other          also save work/no_drums.wav
    --force               ignore the cache and rerun every stage

drums2midi resimplify <job-dir> [--count-in 0 | --to DIR | …]   # regenerate outputs from the cached analysis
drums2midi lyrics <job-dir> [--artist … --title … --offset …]  # redo only the lyrics
drums2midi fx-template <Title.RPP>                               # remember your drum VSTi
drums2midi doctor
```

Inputs: wav, mp3, m4a, aac, flac, aiff, ogg, opus, or any URL `yt-dlp` understands. The same
file or URL always maps to the same job folder.

## What you get

```
~/Music/Drums2MIDI/Juanes - Fotografia (4b74e33d)/
├── Fotografia.RPP            ← the only thing you open
├── input/
│   ├── source.webm           what yt-dlp downloaded (or your file, copied as-is)
│   ├── source.json           where it came from
│   └── original.wav          44.1 kHz stereo; everything is analysed from this
├── work/                     intermediate; safe to delete, a re-run rebuilds it
│   ├── drums.wav · vocals.wav · activations.npy · beats.json
│   ├── notes.json · notes-daring.json · lyrics.json · lyrics.txt
│   └── lyrics-original.lrc   song time (no count-in), for the untouched original.wav
└── output/
    ├── Fotografia.mid        the drums, conservative detection, with count-in
    ├── Fotografia (daring).mid
    ├── Fotografia.lrc        ┐ here unless --to was given — then they are in
    └── Fotografia.wav        ┘ the song folder (the .wav once you have rendered)
```

### The Reaper project

A practice mix: MIDI drums through your instrument in the centre, the original song quiet on
the right as a reference, the real drum stem muted for A/B checks.

| Track | Content | Pan | State |
|---|---|---|---|
| `<Title> (drums)` | `work/drums.wav`, the real drums | hard left | muted, −9 dB |
| `<Title> (MIDI conservative)` | the drums as MIDI | **centre** | on, your VSTi loaded |
| `<Title> (MIDI daring)` | same, more sensitive detection | hard right | muted, your VSTi loaded |
| `<Title> (original)` | `input/original.wav` | right (0.6) | on, −16 dB |

Every lyric line is a marker on the ruler. Media paths are relative, so the job folder can be
moved; the render target is absolute and is whatever `--to` (or `output/`) was.

**Your edits are safe.** Once Reaper has saved `<Title>.RPP`, drums2midi never writes to it again;
a regeneration (`resimplify`, `lyrics`) goes to `<Title>.new.RPP` next to it.

### Your drum instrument

Set it up once: open a generated project, put your drum VSTi on the MIDI track, pick the kit,
save. Then:

```bash
drums2midi fx-template "~/Music/Drums2MIDI/<job>/<Title>.RPP"
```

The FX chain — plugin and state, kit included — is stored in
`~/.config/drums2midi/midi-track.RfxChain` and pre-loaded on both MIDI tracks of every project
generated from then on. `drums2midi fx-template` with no argument shows what is stored;
`--clear` removes it. A Reaper-exported `.RfxChain` dropped at that path works too.

### The count-in

Two bars of side stick at the song's tempo, ending exactly on the song's first downbeat; the
song's bar 1 is bar 3 of the project and bar lines land on the song's bars. The `.lrc` starts
with the count-in as a visible countdown — one line per click, `1 2 3 4 · 1 2 3 4` — so the
highlighted line ticks along before the first lyric. The CLI prints where the song starts
(`4.500s`) in case you line things up by hand elsewhere.

### Conservative vs daring

The conservative MIDI uses the transcriber's default thresholds and rarely invents hits, but it
is shy on toms and cymbals. The daring one lowers the thresholds (most on toms and cymbals) and
catches more of what is there at the cost of some false positives. Velocity follows the model's
confidence, so the extra, less certain hits come in quiet. Flip the mutes on the two MIDI tracks
to compare.

GM notes: kick 36, snare 38, toms 45, hi-hat 42, cymbals 49, count-in 37.

### Lyrics

The song is identified from `--artist/--title`, the file's tags, the YouTube title and channel,
or the file name, and looked up on [LRCLIB](https://lrclib.net) (open, no account). Candidates
are ranked by name similarity, duration and whether they are time-synced. The result is checked
against the isolated vocal stem: a gross mismatch (a music video with a longer intro than the
album cut, say) is corrected with a constant shift; small disagreements are left alone, because
LRC lines are conventionally tagged a touch early. `work/lyrics.json` records what was
considered and why. Lines also go into the project as markers and into the `.mid` files as
lyric events.

Studio recordings only: the timing comes from a database, so a live or cover version gets the
right words at the wrong times.

## How it works

```
file / URL ─ffmpeg→ original.wav ─Demucs→ drums.wav ─ADTOF→ activations ─peak picking→ notes
                         │                └→ vocals.wav ─→ lyrics timing check
                         └──Beat This!→ beats / downbeats / BPM ─┐
                                                                 └→ <Title>.mid · .lrc · .RPP
```

- [Demucs](https://github.com/facebookresearch/demucs) (`htdemucs`) separates drums and vocals.
- [ADTOF-pytorch](https://github.com/xavriley/ADTOF-pytorch) transcribes the drum stem into
  five classes at 100 frames/s.
- [Beat This!](https://github.com/CPJKU/beat_this) finds beats and downbeats on the full mix.
- One `Sequence` (count-in, shift, note ticks) feeds both the `.mid` writer and the Reaper
  project, so they can never disagree.

## Limitations

- Five drum classes: all toms are one note, crash and ride are one note, no open/closed hi-hat.
  A ride pattern shows up as a run of crashes on note 49.
- Constant tempo for now; a per-beat tempo map is next.
- Notes are not quantized yet ("raw"); grid-snapped `essence`/`detailed` styles are planned.
- Dense mixes (metal, busy electronic) give messier hi-hats.
- Lyrics need the song to exist on LRCLIB; originals and obscure songs come back "not found"
  (a Whisper fallback is planned).
- `yt-dlp` breaks when YouTube changes; `mise upgrade yt-dlp` fixes it.

## Development

```bash
mise run test        # fast tests (~1 s)
mise run test-slow   # includes the full pipeline on a synthetic loop
mise run lint
```

See `PLAN.md` for the design and roadmap, `AGENTS.md` for the working conventions.
