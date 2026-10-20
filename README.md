# drums2midi

Paste a YouTube link or point at an audio file → get a simplified, grid-locked drum MIDI
file (with a count-in) and a Reaper project that loads it alongside the original audio.

See [PLAN.md](PLAN.md) for the design.

## Setup

```bash
mise install          # Python 3.12, uv, ffmpeg, yt-dlp, then `uv sync` for the Python deps
drums2midi doctor     # verify everything is wired up
```

## Usage

```bash
drums2midi run song.mp3
drums2midi run "https://youtu.be/…"
drums2midi resimplify ~/Music/Drums2MIDI/<job>/ --style detailed --grid 8
```
