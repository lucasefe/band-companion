# AGENTS.md — drums2midi

Python CLI that turns an audio file (YouTube links planned) into drum MIDI plus a Reaper
project. No GUI. `PLAN.md` is the design document; this file is the operating manual.

## Commands

```bash
mise install            # toolchain (python 3.12, uv, ffmpeg, yt-dlp) + `uv sync` via postinstall
drums2midi doctor       # all green = environment is fine
drums2midi run <file|url>   # full pipeline → ~/Music/Drums2MIDI/<title> (<hash>)/
drums2midi resimplify <job-dir> [--count-in 0 ...]   # reruns only simplify/output
drums2midi lyrics <job-dir> [--artist .. --title .. --offset ..]   # reruns only lyrics/output

mise run test           # fast suite (~1 s; ADTOF weights are bundled)
mise run test-slow      # also runs Demucs + Beat This! end to end (needs cached/downloaded weights)
mise run lint           # ruff check + format --check
mise run fmt            # ruff format + autofix
uv run pytest tests/test_midi_out.py -k count_in   # single test
```

The `.venv` is auto-activated by mise when you `cd` in; `uv run` works regardless.
Reference material used for tuning:
- `/Users/lucasefe/Code/lucasefe/band-companion/tracks/Cumple de Guille/01 - No podrás/NoPodras.wav`
  (Cristian Castro; 120 BPM, 4/4, first downbeat 1.46 s; job `~/Music/Drums2MIDI/NoPodras (b1660813)`).
  A hand-made `NoPodras.lrc` sits next to it for comparing lyrics timing.
- `https://www.youtube.com/watch?v=flr8vDQ9Wuc` (Juanes – Fotografía, music video; 244 s vs the
  241 s album cut → lyrics offset +0.80 s is expected and correct).
The consumer of `lyrics.lrc` is Band Companion (`~/Code/lucasefe/band-companion`): line-level
`[mm:ss.xx]text`, untimed lines ignored.

## Layout

```
src/drums2midi/
  cli.py        typer app: run / resimplify / doctor. Rich output to stderr; stdout stays empty.
  pipeline.py   stage orchestration + caching (_Runner), RunOptions/RunResult, profile file names
  job.py        job folder, manifest.json, ARTIFACTS per stage, is_done()/mark_done()
  ingest.py     file or URL → original.wav (ffmpeg; URLs via `yt-dlp` subprocess, --dump-json first)
  separate.py   Demucs htdemucs → drums.wav + vocals.wav (MPS, CPU fallback, progress callback)
  transcribe.py ADTOF → activations.npy [frames, 5] @ 100 fps
  tempo.py      Beat This! → beats.json (beats, downbeats, bpm, meter)
  simplify.py   peaks → Note list; PROFILES (conservative/daring thresholds); styles (raw only so far)
  lyrics.py     Line/Lyrics, LRC parse/format, song Hints, lrclib lookup, vocal-stem offset check
  lrclib.py     lrclib.net client (retries, UA) + candidate ranking
  midi_out.py   CountIn, TempoMap, Layout, build_sequence() → Sequence; write_midi() (+ lyric events)
  reaper.py     write_rpp(): stem L, MIDI tracks R, original muted; MIDI events embedded; MARKERs
  audio.py / devices.py   small helpers
tests/          pytest; fixtures/make_loop.py synthesizes a 100 BPM loop with known hits
```

## Architecture rules

- **Stages are cached per job.** Each stage lists its artifacts in `job.ARTIFACTS` and its
  parameters in the `stage()` call. Once a stage runs, every later stage runs too (`_Runner.dirty`).
  If you add an output file, add it to `ARTIFACTS` or it will never be regenerated. Params must
  be JSON-round-trippable (lists, not tuples) or the cache will never hit.
- **One `Sequence`, two renderers.** `build_sequence()` is the single source of truth for ticks
  (count-in, shift, note events). `write_midi()` and `reaper.py` both consume it; never compute
  timing in a renderer.
- **Song time vs output time.** `notes.json` and `beats.json` are in song time (seconds into
  `original.wav`). `Layout.shift` is added only when rendering. Audio items in the RPP sit at
  `shift`; the count-in ends exactly on the first downbeat.
- **Profiles.** `PROFILES` in `simplify.py` maps a name to per-class thresholds
  `(kick, snare, tom, hihat, cymbal)`. `pipeline.PRIMARY` (`conservative`) is `drums.mid` and the
  unmuted Reaper track; other profiles get `-<name>` suffixed files and muted tracks.
- **Lyrics stage never fails the run.** It always writes `lyrics.json` (`found: false` with a
  `reason`/`error` on a miss) so it caches like every other stage. It runs right before `output`
  so redoing lyrics does not rerun `simplify`. Lines are stored in song time
  (`lines_song_time`); `output` adds `Layout.shift` for markers and MIDI lyric events.
- **Offset rule.** `estimate_offset()` applies a shift only if |shift| ≥ 0.5 s (gross mismatch);
  smaller suggestions are reported, not applied — LRC convention tags lines slightly early.
- **Saved options.** `_Runner` writes `RunOptions` into `manifest.json["options"]`;
  `resimplify`/`lyrics` merge CLI overrides onto them (`options_from_job`) so stage params stay
  stable and caches hit.
- **Models get tensors/paths we control.** Audio I/O goes through `soundfile`/ffmpeg, not
  torchaudio or demucs loaders (torchaudio is frozen at 2.11 while torch moves on).
- Heavy imports (torch, demucs, beat_this) live inside functions so `--help` and `doctor` stay fast.

## Dependency gotchas (all already handled; don't undo)

- `[tool.uv] environments` excludes Intel macOS. Without it uv's universal lock downgrades torch
  to 2.2 and numpy to 1.x for everyone because demucs pins those only on x86 macOS.
- `adtof-pytorch` is a git dependency pinned by commit. Its `load_pytorch_weights` prints to stdout;
  `transcribe.py` wraps it in `redirect_stdout`. Its `PeakPicker.pick()` wants a `[1, T, 5]` batch.
  Class order is `[kick, snare, tom, hihat, cymbal]` = ADTOF labels `[35, 38, 47, 42, 49]`.
- ADTOF only accepts `device="cuda"|"cpu"`; we run it on CPU (a few seconds per song).
- Beat This! and Demucs weights download on first use into `~/.cache/torch/hub/checkpoints/`.
- `yt-dlp` and `ffmpeg` come from mise, not pip. `mise upgrade yt-dlp` when YouTube breaks.
- The "unauthenticated requests to the HF Hub" warning is a server header logged by
  huggingface_hub, whose logger resets its level on (lazy) import; `HF_HUB_VERBOSITY=error`
  set in `cli.main()` is what silences it.
- LRCLIB returns 503 `ServerOverloaded` sporadically; `lrclib._get` retries with backoff.
  `/api/get` needs exact keys; we use `/api/search` and rank ourselves.
- MIDI lyric events are written UTF-8 (`MidiFile(charset="utf-8")`); read them back the same way.

## Reaper project format

Derived from a project saved by Reaper 7.82 and locked by `tests/test_reaper.py`:
- `VOLPAN <linear vol> <pan -1..1> -1 -1 1`, `MUTESOLO <mute> 0 0`.
- In-project MIDI: `<SOURCE MIDI` / `HASDATA 1 480 QN` / `E <delta ticks> <status> <d1> <d2>` (hex,
  lowercase) / final `E <delta> b0 7b 00` marks the source end / `IGNTEMPO 0 <bpm> <num> 4`.
- GUIDs are `{UPPERCASE-UUID4}`; Reaper fills in every field we omit.

## Conventions

- Python 3.12, ruff (line length 100, rules E/F/I/UP/B; B008 ignored in cli.py for typer).
- Dataclasses for data, functions for stages. `from __future__ import annotations` everywhere.
- Comments only where behaviour isn't obvious from the code.
- Tests: pure logic tests use the `loop_beats` fixture (ground truth, no model); anything that
  downloads weights is `@pytest.mark.slow` (deselected by default via `addopts`).
- Commit messages: imperative subject, body explains the why. Commit after each working increment.

## Status / next

Done: M0 tooling, M1 (`run` → MIDI with count-in), Reaper project, conservative + daring
profiles, YouTube ingest, lyrics (LRCLIB + vocal-stem offset check → .lrc, markers, MIDI events).
Open (see `PLAN.md` §4–5): `essence`/`detailed` quantized styles and grid, per-beat tempo map
(`TempoMap` already supports segments; RPP needs `<TEMPOENVEX>` points), `--bpm` /
`--first-downbeat` / `--downbeat-offset` overrides, Whisper fallback for lyrics, `--json`
progress, fuller `doctor`.
