# drums2midi — Plan

A command-line tool: give it an audio file or a YouTube link → get a playable, simplified drum
MIDI file (the "essence" of the groove, with a count-in) plus a Reaper project that loads it
next to the original audio.

Goal is **useful, not perfect**: kick/snare/hats locked to the song's grid, sensible dynamics,
no jittery ghost-note noise. The MIDI file is the deliverable; the Reaper project is a
convenience. Cleanup happens in Reaper.

---

## 1. Decisions

| Topic | Decision | Why |
|---|---|---|
| Interface | **CLI only**, no GUI | Everything is testable from the terminal; a UI can wrap it later. |
| Language | **Python** (not Go) | Demucs and ADTOF are PyTorch with Python-only APIs. A Go CLI would spawn Python anyway: two toolchains for zero gain. |
| Tooling | **mise** owns the toolchain | `git clone` → `mise install` → done. Python 3.12, uv, ffmpeg (conda-forge prebuilt) and yt-dlp (standalone binary) all come from mise; a `postinstall` hook runs `uv sync` for the Python deps. |
| Separation | **Demucs 4.1** (`htdemucs`, `htdemucs_ft` for `--quality best`) | Best open drum-stem separation. Runs on MPS, falls back to CPU. |
| Transcription | **ADTOF-pytorch** (pinned commit) | 5-class CRNN with bundled weights (~3.6 MB). Fast on CPU. |
| Tempo / beats | **Beat This!** (ISMIR 2024) | Gives beats **and downbeats** directly; no madmom. `--bpm` overrides detection entirely. |
| yt-dlp | subprocess to the mise-managed binary | YouTube breaks old yt-dlp often; `mise upgrade yt-dlp` fixes it without touching the Python lockfile. |
| Count-in | default **2 bars** of side stick before the first downbeat | Needed for playing along; also aligns Reaper's bar lines with the song. |
| Audio I/O | ffmpeg → WAV, then `soundfile` → numpy/torch tensors | We never rely on torchaudio/demucs file loaders (fragile across torch versions). Models receive tensors. |

---

## 2. Setup

```bash
mise install          # Python 3.12, uv, ffmpeg, yt-dlp, then uv sync → .venv
drums2midi doctor     # all green?
```

`mise.toml`:
- `[tools]` python 3.12, uv, ffmpeg 9 (conda-forge), yt-dlp (GitHub release binary).
- `[env] UV_PYTHON_DOWNLOADS=never` — uv must use mise's Python.
- `[settings] python.uv_venv_auto` — `.venv` is activated on `cd`, so `drums2midi` and `pytest`
  are on PATH without `uv run`.
- `[hooks] postinstall = "uv sync"`; `[tasks]` `sync`, `test`, `test-slow`, `lint`, `fmt`.

Model weights download on first use into the standard torch/HF caches and are reported as a
"downloading model" step: Demucs htdemucs ≈ 80 MB, Beat This! `final0` ≈ 78 MB, ADTOF bundled.

---

## 3. Layout

```
extract-drums/
├── mise.toml
├── pyproject.toml / uv.lock
├── PLAN.md / README.md
├── src/drums2midi/
│   ├── cli.py          # run / resimplify / doctor (typer + rich)
│   ├── job.py          # job folder, manifest, per-stage caching
│   ├── ingest.py       # file or URL → original.wav (44.1 kHz stereo)
│   ├── separate.py     # Demucs → drums.wav
│   ├── transcribe.py   # ADTOF → activations.npy
│   ├── tempo.py        # Beat This! (or --bpm) → beats.json
│   ├── simplify.py     # peaks → quantized, tiered GM drum notes → notes.json
│   ├── midi_out.py     # drums.mid (count-in, tempo map)
│   └── reaper.py       # project.RPP
└── tests/
    ├── fixtures/make_loop.py
    ├── test_transcribe.py
    ├── test_tempo.py
    ├── test_simplify.py
    ├── test_midi_out.py
    ├── test_reaper.py
    └── test_pipeline.py   # @slow
```

---

## 4. CLI

```bash
drums2midi run <file-or-url>
    [--out DIR]                        # default ~/Music/Drums2MIDI/<title> (<hash>)/
    [--style essence|detailed|raw]     # default essence
    [--grid 16|8|12|24]                # 16ths, 8ths, triplet 8ths, triplet 16ths
    [--quality fast|best]              # htdemucs | htdemucs_ft (~4× slower)
    [--bpm N] [--first-downbeat SEC]   # skip tempo detection
    [--downbeat-offset N]              # shift detected downbeat by N beats
    [--count-in BARS] [--count-in-note 37]   # default 2 bars; 0 disables
    [--constant-tempo]                 # single tempo instead of a per-beat tempo map
    [--device auto|mps|cpu]
    [--keep-other]                     # also save the no_drums stem
    [--json]                           # JSON-lines progress on stdout (for scripting)

drums2midi resimplify <job-dir> [--style …] [--grid …] [--count-in …] [--bpm …] …
    # reuses cached stems/activations; reruns tempo only if --bpm changed; instant otherwise

drums2midi doctor
    # ffmpeg, yt-dlp, torch + device, demucs, adtof, beat_this, cached weights
```

- Progress and logs go to **stderr** (rich). stdout is empty unless `--json`.
- Exit 0 on success, non-zero on error. At the end, print the job folder, the hit counts per
  drum, BPM, and the **audio offset** (see count-in) so the `.mid` can be lined up by hand in
  any DAW.

---

## 5. Pipeline

```
input (file | URL)
  → ingest      yt-dlp (URLs) / ffmpeg            → original.wav
  → separate    Demucs --two-stems=drums          → drums.wav
  → transcribe  ADTOF on drums.wav                → activations.npy   [T × 5 @ 100 fps]
  → tempo       Beat This! on original.wav        → beats.json        (beats, downbeats, bpm)
  → simplify    peak pick + quantize + tiers + GM → notes.json
  → output      drums.mid + project.RPP
```

Every stage writes its artifact into the job folder and is **skipped if the artifact already
exists** (`manifest.json` records the input hash and the parameters each artifact depends on).
That is what makes `resimplify` instant and lets a stage be re-run after tweaking it without
waiting for Demucs again.

### ingest
- URL → `yt-dlp -f bestaudio -x --audio-format wav --print-json --newline --progress-template …`
  via subprocess. Parse the JSON for the title; parse progress lines for the bar.
- File → `ffmpeg -i in -ac 2 -ar 44100 original.wav`. Accept wav/mp3/m4a/aac/flac/aiff/ogg.
- Job folder name: `<sanitized title> (<8-char hash of the source>)`.

### separate
- `demucs.api.Separator(model=…, device=…)`, `separate_tensor` on the loaded WAV.
- Device `auto`: mps → cpu. Catch MPS failures and retry on CPU once.
- Save `drums.wav`; `no_drums.wav` only with `--keep-other`.

### transcribe
- `adtof_pytorch.transcribe_to_midi(drums_wav, tmp, device="cpu", return_activations=True)`
  → `[1, T, 5]` at 100 fps. Save `activations.npy`.
- Class order (ADTOF `LABELS_5`): `[35 kick, 38 snare, 47 toms, 42 hi-hat, 49 cymbals]`.
- CPU only (the library accepts cuda/cpu); small CRNN, a few seconds per song on M-series.

### tempo
- Default: `beat_this.inference.File2Beats(checkpoint_path="final0", device=…, dbn=False)` on
  `original.wav` (full mix tracks better than the stem). Store `beats`, `downbeats`,
  `bpm = 60 / median(diff(beats))`, and `meter` (beats between downbeats; expect 4).
- `--tempo-model small` → `small0` (8 MB) for speed.
- `--bpm N`: constant grid. Phase = the beat offset in `[0, 60/N)` that best aligns with the
  ADTOF kick+snare peaks; downbeat = the phase among 4 with kicks on 1/3 and snares on 2/4.
  `--first-downbeat SEC` pins it exactly.
- `--downbeat-offset N` shifts the chosen downbeat by N beats (positive or negative).
- Grid: subdivide each inter-beat interval into `grid/4` slots → follows tempo drift.

### simplify (the "essence" logic — the core)
- Peak picking: `adtof_pytorch.post_processing.PeakPicker`, default per-class thresholds
  `[0.22, 0.24, 0.32, 0.22, 0.30]`. Keep the activation value at each peak as confidence.
- Styles:
  - `raw`: ADTOF output, unquantized, velocity from confidence.
  - `detailed`: quantize to grid, keep everything, 4 velocity tiers.
  - `essence` (default):
    - Snap each hit to the nearest slot if within 40 % of a slot; otherwise drop it.
    - One hit per (class, slot) — keep the most confident.
    - Thresholds +0.1 for hats/toms/cymbals (drops ghost notes).
    - Velocity in 3 tiers from per-class confidence percentiles: 70 / 95 / 118.
    - Kick + snare on the same slot: keep both. Hat on the same slot as a cymbal: drop the hat.
- GM mapping: kick → **36**, snare **38**, toms → **45** (ADTOF merges all toms),
  hi-hat **42** (closed; no open/closed split), cymbals → **49** (crash; crash+ride merged).
- Note length: 1/32 at local tempo. `notes.json` stores notes in **song time** (no count-in).

### output: drums.mid
- `mido`, one track, channel 10 (index 9), 480 PPQ.
- **Count-in** (default 2 bars, `--count-in 0` to disable): GM side stick 37, beat 1 at
  velocity 110, others 85, at the tempo of the song's first bar. It ends **exactly on the
  song's first downbeat**:
  ```
  shift = max(0, count_in_duration − first_downbeat_time)
  ```
  All drum notes move forward by `shift`. The count-in therefore starts at tick 0 and the
  song's bar 1 begins at Reaper bar `count_in + 1`, so **measure lines land on the song's
  bars**. `shift` is printed at the end of the run as the offset to apply to the audio if the
  `.mid` is used on its own.
- Tempo: a tempo map with one `set_tempo` per beat (so the DAW grid follows the song), or a
  single tempo with `--constant-tempo` / `--bpm`.
- Time signature event from the detected meter (4/4 normally).

### output: project.RPP
Plain-text Reaper project:
- Project tempo = global BPM, time signature from meter.
- Track 1 "Original": audio item → `original.wav`, position = `shift`, volume −6 dB.
- Track 2 "Drums (stem)": audio item → `drums.wav`, position = `shift`, muted.
- Track 3 "Drums MIDI": MIDI item at 0 referencing `drums.mid`
  (`<SOURCE MIDI FILE "…">`). No FX for now.
- Verified by opening in Reaper, then locked with a golden-file test. Once a drum instrument is
  added in Reaper and the project saved, that `.RPP` becomes the template for an FX block
  (`--instrument` flag later).

### Job folder

```
~/Music/Drums2MIDI/<title> (<hash>)/
├── manifest.json      # source, hash, params per artifact, timings
├── original.wav
├── drums.wav
├── activations.npy
├── beats.json
├── notes.json
├── drums.mid          ← the deliverable
└── project.RPP
```

---

## 6. Tests

- `tests/fixtures/make_loop.py`: 100 BPM, 16-beat loop (kicks on 1 & 3, snares on 2 & 4,
  8th-note hats, bass) — see Appendix. Generated on demand, git-ignored.
- **transcribe**: ADTOF alone on the loop finds 8 kicks, 8 snares, 32 ± 2 hats.
- **tempo**: Beat This! on the loop → BPM within ±1 of 100, first downbeat at ≈ 0.0 s;
  `--bpm 100` path produces the same grid.
- **simplify**: after `essence`, every hit is exactly on a 16th slot, no (class, slot)
  duplicates, hat dropped where a cymbal exists.
- **midi_out**: count-in has 8 side-stick notes, last one exactly one beat before the first
  kick; with `--count-in 0` the first kick is at the first downbeat time; tempo map matches
  beats.json.
- **reaper**: golden-file comparison of `project.RPP`.
- **pipeline** (`@slow`, needs weights): a short CC track → non-empty kick/snare/hat output,
  RPP references existing files.

---

## 7. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M0 | Tooling | `mise install` from a clean clone → `drums2midi doctor` all green. |
| M1 | `run file.wav` → `drums.mid` (raw style, constant tempo, count-in) | Demucs runs on MPS here (weights download verified); loop tests pass; a real song opens in Reaper and grooves with the original. |
| M2 | Tempo map, `essence`/`detailed`, downbeat/count-in alignment, `resimplify`, `project.RPP` | Opening `project.RPP` shows 3 aligned tracks; Reaper's bar lines match the song; switching style is instant. |
| M3 | YouTube URLs, `--bpm`, full `doctor`, `--json`, polish | `drums2midi run "https://youtu.be/…"` works end to end with live progress. |

Tune `essence` on real songs between M2 and M3 — that is where the product lives.

---

## 8. Known limitations (say so in `--help`)

- ADTOF has 5 classes: toms merged, crash/ride merged, no open-hat detection.
- Busy mixes (metal, dense electronic) give messier hats.
- Assumes a steady meter; songs with meter changes need manual grid fixes in Reaper.
- Downbeat detection can land on beat 3 → `--downbeat-offset 2`.
- YouTube downloads are for personal practice; yt-dlp needs periodic `mise upgrade yt-dlp`.

## 9. Later

- `--instrument` to inject a drum VSTi FX chain into the RPP (template from a user-saved project).
- LarsNet per-piece separation → open hats, individual toms, ride vs crash.
- Bass-to-MIDI from the `no_drums` stem (Basic Pitch).
- ReaScript to import straight into the currently open Reaper project.
- Core ML port of ADTOF / Beat This! if a Python-free build is ever wanted.

---

## Appendix: test loop generator

```python
import numpy as np, soundfile as sf

sr = 44100
bpm = 100
beat = 60 / bpm
dur = beat * 16
t = np.arange(int(sr * dur)) / sr
y = np.zeros_like(t)
rng = np.random.default_rng(0)


def add(start, sig):
    i = int(start * sr)
    n = min(len(sig), len(y) - i)
    y[i : i + n] += sig[:n]


tt = np.arange(int(0.3 * sr)) / sr
kick = np.sin(2 * np.pi * (50 + 100 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 12)
snare = (
    rng.normal(0, 1, len(tt)) * np.exp(-tt * 20) * 0.5
    + np.sin(2 * np.pi * 180 * tt) * np.exp(-tt * 25) * 0.4
)
hat = rng.normal(0, 1, int(0.05 * sr))
hat = np.diff(hat, prepend=0) * np.exp(-np.arange(len(hat)) / sr * 80) * 0.15
for b in range(16):
    s = b * beat
    if b % 4 in (0, 2):
        add(s, kick)
    if b % 4 in (1, 3):
        add(s, snare)
    add(s, hat)
    add(s + beat / 2, hat)
bass = 0.2 * np.sin(2 * np.pi * 55 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * t / beat)))
sf.write("loop.wav", np.stack([y + bass] * 2, 1) * 0.6, sr)
```

Expected ADTOF output: 8 kick, 8 snare, ~32 hi-hat. Expected tempo ≈ 100 BPM, first downbeat at 0.
