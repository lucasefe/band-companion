import { parseLyrics, type Lyrics } from './lyrics'
import audioManifest from '../tracks/audio.json'

export interface Track {
  id: string
  title: string
  artist: string | null
  // Where the song was imported from (a YouTube page, usually); shown as an outbound link.
  source: { url: string; label: string } | null
  url: string
  bytes: number
  // The original song on the same timeline (count-in included); the leader can mix it in.
  reference: { url: string; bytes: number } | null
  lyrics: Lyrics | null
  setId: string
  setName: string
  order: number
}

export interface TrackSet {
  id: string
  name: string
  order: number
  tracks: Track[]
}

interface AudioEntry {
  key: string
  bytes: number
  reference?: { key: string; bytes: number }
}

const DEFAULT_SET = 'Songs'

// Each track is a folder: tracks/<set>/<NN - Title>/ holding one audio file
// (uploaded to R2 by scripts/sync-audio.mjs, listed in tracks/audio.json)
// and optionally one .lrc or .txt with the lyrics.
//
// Folder names are normalized to NFC everywhere: macOS reports NFD, git checks
// out NFC, and the folder path doubles as the track id shared with followers.
const audio = Object.fromEntries(
  Object.entries(audioManifest as Record<string, AudioEntry>).map(([folder, entry]) => [folder.normalize('NFC'), entry]),
)

const lyricFiles = import.meta.glob('/tracks/**/*.{lrc,txt}', {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

// Written by `backline add` next to the lyrics: artist, title and where the song came from.
interface SongRecord {
  song?: { artist?: string | null; title?: string | null }
  source?: { kind?: string; url?: string }
}

const recordFiles = import.meta.glob('/tracks/**/backline.json', {
  eager: true,
  import: 'default',
}) as Record<string, SongRecord>

function loadRecords(): Map<string, SongRecord> {
  const result = new Map<string, SongRecord>()
  for (const [path, record] of Object.entries(recordFiles)) result.set(folderOf(path), record)
  return result
}

function sourceOf(record: SongRecord | undefined): Track['source'] {
  const url = record?.source?.kind === 'url' ? record.source.url : undefined
  if (!url) return null
  try {
    const host = new URL(url).hostname.replace(/^www\./, '')
    return { url, label: /(^|\.)youtu(\.be|be\.com)$/.test(host) ? 'YouTube' : host }
  } catch {
    return null
  }
}

function folderOf(path: string): string {
  const parts = path.replace(/^\/tracks\//, '').split('/')
  parts.pop()
  return parts.join('/').normalize('NFC')
}

// "01 - Sing Sing Sing" -> { order: 1, name: "Sing Sing Sing" }
function parseName(raw: string): { order: number; name: string } {
  const match = raw.match(/^\s*(\d+)\s*[-._)]*\s*(.*?)\s*$/)
  if (match && match[2]) return { order: Number(match[1]), name: match[2] }
  return { order: Number.POSITIVE_INFINITY, name: raw.trim() }
}

function compare(a: { order: number; name: string }, b: { order: number; name: string }): number {
  if (a.order !== b.order) return a.order - b.order
  return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })
}

function loadLyrics(): Map<string, Lyrics> {
  const result = new Map<string, Lyrics>()
  for (const [path, text] of Object.entries(lyricFiles)) {
    const folder = folderOf(path)
    const kind = path.toLowerCase().endsWith('.lrc') ? 'lrc' : 'txt'
    // An .lrc wins over a .txt in the same folder.
    if (result.has(folder) && kind === 'txt') continue
    result.set(folder, parseLyrics(text, kind))
  }
  return result
}

export function loadSets(): TrackSet[] {
  const sets = new Map<string, TrackSet>()
  const lyrics = loadLyrics()
  const records = loadRecords()

  for (const [folder, entry] of Object.entries(audio)) {
    const parts = folder.split('/')
    const trackFolder = parts.pop()!
    const setFolder = parts.join('/') || DEFAULT_SET
    const setInfo = parseName(setFolder)
    const trackInfo = parseName(trackFolder)

    let set = sets.get(setFolder)
    if (!set) {
      set = { id: setFolder, name: setInfo.name, order: setInfo.order, tracks: [] }
      sets.set(setFolder, set)
    }
    const record = records.get(folder)
    set.tracks.push({
      id: folder,
      title: trackInfo.name,
      artist: record?.song?.artist?.trim() || null,
      source: sourceOf(record),
      url: `/audio/${entry.key}`,
      bytes: entry.bytes,
      reference: entry.reference ? { url: `/audio/${entry.reference.key}`, bytes: entry.reference.bytes } : null,
      lyrics: lyrics.get(folder) ?? null,
      setId: setFolder,
      setName: setInfo.name,
      order: trackInfo.order,
    })
    lyrics.delete(folder)
  }

  for (const folder of lyrics.keys()) {
    console.warn(`Lyrics without audio in tracks/${folder}; run npm run sync-audio.`)
  }

  const result = [...sets.values()]
  for (const set of result) {
    set.tracks.sort((a, b) =>
      compare({ order: a.order, name: a.title }, { order: b.order, name: b.title }),
    )
  }
  result.sort(compare)
  return result
}

export function allTracks(sets: TrackSet[]): Track[] {
  return sets.flatMap((s) => s.tracks)
}

export function findTrack(sets: TrackSet[], id: string): Track | null {
  for (const set of sets) {
    const track = set.tracks.find((t) => t.id === id)
    if (track) return track
  }
  return null
}

// "Artist · Set" under a title; the set only when there is more than one.
export function describe(track: Track, withSet: boolean): string {
  return [track.artist, withSet ? track.setName : null].filter(Boolean).join(' \u00b7 ')
}
