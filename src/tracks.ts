export interface Track {
  id: string
  title: string
  url: string
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

const DEFAULT_SET = 'Songs'

// Lossless sources (wav/aiff/flac) are converted by scripts/convert.mjs and
// deliberately not listed here.
const files = import.meta.glob('/tracks/**/*.{mp3,m4a}', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>

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

export function loadSets(): TrackSet[] {
  const sets = new Map<string, TrackSet>()

  for (const [path, url] of Object.entries(files)) {
    const parts = path.replace(/^\/tracks\//, '').split('/')
    const file = parts.pop()!
    const folder = parts.join('/') || DEFAULT_SET
    const setInfo = parseName(folder)
    const trackInfo = parseName(file.replace(/\.[^.]+$/, ''))

    let set = sets.get(folder)
    if (!set) {
      set = { id: folder, name: setInfo.name, order: setInfo.order, tracks: [] }
      sets.set(folder, set)
    }
    set.tracks.push({
      id: path,
      title: trackInfo.name,
      url,
      setId: folder,
      setName: setInfo.name,
      order: trackInfo.order,
    })
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
