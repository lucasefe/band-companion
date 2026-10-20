# drums2midi

Point it at a song (file or YouTube link) → get the drums as MIDI with a count-in, the lyrics
as a timed `.lrc`, and a Reaper project that lets you hear the real drums in one ear and the
MIDI in the other with the lyrics on the ruler.

```bash
drums2midi run "~/Music/No Podrás.wav"
drums2midi run "https://www.youtube.com/watch?v=flr8vDQ9Wuc"
```

```
✓ ingest      ━━━━━━━━━━━━━━━━━━━━━━━━ 0.2s
✓ separate    ━━━━━━━━━━━━━━━━━━━━━━━━ 11.0s   # Demucs on Apple Silicon GPU
✓ transcribe  ━━━━━━━━━━━━━━━━━━━━━━━━ 2.9s    # ADTOF
✓ tempo       ━━━━━━━━━━━━━━━━━━━━━━━━ 1.2s    # Beat This!
✓ simplify    ━━━━━━━━━━━━━━━━━━━━━━━━ 0.4s
✓ lyrics      ━━━━━━━━━━━━━━━━━━━━━━━━ Cristian Castro – No Podras · 41 lines
✓ output      ━━━━━━━━━━━━━━━━━━━━━━━━ 0.0s
title         NoPodras
tempo         120 bpm, 4/4, first downbeat at 1.46s
conservative  kick 257  snare 247  tom 20  hihat 673  cymbal 55
daring        kick 261  snare 260  tom 75  hihat 808  cymbal 129
lyrics        Cristian Castro – No Podras · 41 timed lines · lrclib:24332759 · on-vocal 93%
count-in      4.00s; audio starts at 2.540s in the DAW timeline
midi          ~/Music/Drums2MIDI/NoPodras (b1660813)/drums.mid
reaper        ~/Music/Drums2MIDI/NoPodras (b1660813)/project.RPP
```

A 4-minute song takes about 15 seconds on an M-series Mac. Running it again is instant: every
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

## Usage

```bash
drums2midi run <audio-file> [options]
    --out DIR             root for job folders (default ~/Music/Drums2MIDI)
    --to DIR              deliver the .lrc and the render into this song folder
    --quality fast|best   Demucs htdemucs (default) or htdemucs_ft (~4× slower, a bit cleaner)
    --count-in BARS       bars of side-stick count-in before the first downbeat (default 2; 0 = none)
    --count-in-note N     MIDI note for the click (default 37, GM side stick)
    --device auto|mps|cpu
    --keep-other          also save no_drums.wav
    --force               ignore the cache and rerun everything
    --artist / --title    help the lyrics lookup when tags/file name are not enough
    --lyrics-file FILE    use your own .lrc/.txt instead of looking lyrics up
    --lyrics-offset SEC   shift the lyrics by hand (skips the automatic check)
    --no-lyrics

drums2midi resimplify <job-dir> [--count-in 0 … --to DIR]   # regenerate outputs from cached analysis
drums2midi lyrics <job-dir> [--artist … --title … --offset …]   # redo only the lyrics
drums2midi fx-template <project.RPP>                 # remember your drum VSTi for new projects
drums2midi doctor                                    # check tools, packages, GPU
```

Supported inputs: wav, mp3, m4a, aac, flac, aiff, ogg, opus, or any URL `yt-dlp` understands
(YouTube, …). The same URL always maps to the same job folder.

## What you get

```
~/Music/Drums2MIDI/Juanes - Fotografia (4b74e33d)/
├── Fotografia.RPP            ← the only thing you open; File → Render is pre-targeted at output/
├── input/
│   ├── source.webm           what yt-dlp downloaded (or your file, copied as-is)
│   ├── source.json           where it came from
│   └── original.wav          44.1 kHz stereo; everything is analysed from this
├── work/                     intermediate; safe to delete, a re-run rebuilds it
│   ├── drums.wav · vocals.wav · activations.npy · beats.json
│   ├── notes.json · notes-daring.json · lyrics.json · lyrics.txt
│   └── lyrics-original.lrc   song time, for the untouched original.wav
└── output/                   a Band Companion song folder
    ├── Fotografia.lrc        count-in timeline: countdown, then the song
    ├── Fotografia.wav        ← Reaper renders here
    ├── Fotografia.mid        the drums (conservative), with count-in
    └── Fotografia (daring).mid
```

With `--to "<tracks>/<set>/<NN - Title>"` the `.lrc` is written into that folder and the project's
render target points there, so after rendering the song folder is complete — no copying, no
renaming. The MIDI files stay in the job's `output/`.

**The count-in.** Two bars of side stick at the song's tempo, ending exactly on the song's first
downbeat. The song's bar 1 is therefore bar 3 in the DAW, and bar lines land on the song's bars.
The CLI prints the offset (`audio starts at 2.540s`) in case you line the `.mid` up with the
audio in another DAW by hand.

**The Reaper project** is set up as a practice mix, ready to render:

| Track | Content | Pan | State |
|---|---|---|---|
| `<title> (drums)` | `drums.wav`, the real drums | hard left | muted, −9 dB (unmute to compare) |
| `<title> (MIDI conservative)` | `drums.mid` | **centre** | on — your drum VSTi goes here |
| `<title> (MIDI daring)` | `drums-daring.mid` | hard right | muted |
| `<title> (original)` | `original.wav`, the song as reference | right (0.6) | on, −16 dB |

File → Render is pre-filled (job folder, `<title> (practice).wav`, 24-bit 44.1 kHz, whole
project), so the render is one click and lines up with `lyrics.lrc` — count-in included.

**Your drum instrument.** Set it up once in Reaper on the MIDI track, save the project, then:

```bash
drums2midi fx-template "~/Music/Drums2MIDI/<job>/<Title>.RPP"
```

The FX chain (plugin and its state, e.g. the loaded kit) is stored in
`~/.config/drums2midi/midi-track.RfxChain` and pre-loaded on the MIDI tracks of every project
generated from then on. `drums2midi fx-template` shows what is stored; `--clear` removes it. A
Reaper-exported `.RfxChain` dropped at that path works too.

**Your edits are safe.** Once Reaper has saved `<Title>.RPP`, drums2midi never writes to it again;
a regeneration (`resimplify`, `lyrics`) goes to `<Title>.new.RPP` next to it.

**Band Companion workflow.**

```bash
drums2midi run "https://youtu.be/…" --to "~/Code/band-companion/tracks/Cumple de Guille/02 - Fotografía"
# open Fotografia.RPP → File → Render → Fotografia.wav appears in that folder
cd ~/Code/band-companion && npm run sync-audio
```

**Conservative vs daring.** The conservative file uses the transcriber's default thresholds and
rarely invents hits, but it is shy on toms and cymbals. The daring file lowers the thresholds
(most on toms and cymbals) and catches more of what's there at the cost of some false positives.
Velocity follows the model's confidence, so the extra, less certain hits come in quiet.

**MIDI notes (GM):** kick 36, snare 38, toms 45, hi-hat 42, cymbals 49, count-in 37.

**Lyrics.** The song is identified from your `--artist/--title`, the file's tags, the YouTube
title/channel, or the file name, and looked up on [LRCLIB](https://lrclib.net) (open, no
account). Candidates are ranked by name similarity, duration and whether they are time-synced.
The result is then checked against the isolated vocal stem: if the lines are grossly off (a
music video with a longer intro than the album cut, for instance) a constant shift is applied
automatically; small disagreements are left alone because LRC lines are conventionally tagged a
touch early. `lyrics.json` shows what was considered and why. `lyrics.lrc` sits on the count-in timeline like
everything else — it starts with the count-in as a visible countdown (`1 2 3 4`, one line per
click) and then the song, so it pairs with a backing track that has the count-in;
`lyrics-original.lrc` pairs with the untouched `original.wav`. Lines also go into the Reaper
project as markers and into the `.mid` files as lyric events. Studio recordings only: the
timing comes from a database, so a live or cover version will have the right words at the wrong
times.

## How it works

```
file ─ffmpeg→ original.wav ─Demucs→ drums.wav ─ADTOF→ activations ─peak picking→ notes
                   └────────Beat This!→ beats/downbeats/BPM ──────────────┘
                                                              └→ drums.mid + project.RPP
```

- [Demucs](https://github.com/facebookresearch/demucs) (`htdemucs`) isolates the drums.
- [ADTOF-pytorch](https://github.com/xavriley/ADTOF-pytorch) transcribes the stem into five
  classes at 100 frames/s.
- [Beat This!](https://github.com/CPJKU/beat_this) finds beats and downbeats on the full mix.
- Everything is tied to one timeline so the `.mid` and the `.RPP` can never disagree.

## Limitations

- Five classes only: all toms are one note, crash and ride are one note, no open/closed hi-hat.
  A ride pattern shows up as a run of crashes on note 49.
- Constant tempo for now; the per-beat tempo map is next on the list.
- Notes are not yet quantized ("raw" style); grid-snapped `essence`/`detailed` styles are planned.
- Dense mixes (metal, busy electronic) give messier hi-hats.
- Lyrics need the song to exist on LRCLIB; originals and obscure songs come back "not found"
  (a Whisper fallback is planned). `yt-dlp` needs `mise upgrade yt-dlp` when YouTube changes.

## Development

```bash
mise run test        # fast tests (~1 s)
mise run test-slow   # includes the full pipeline on a synthetic loop
mise run lint
```

See `PLAN.md` for the design and roadmap, `AGENTS.md` for the working conventions.
