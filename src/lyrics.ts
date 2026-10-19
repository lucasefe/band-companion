export interface LyricLine {
  // Seconds from the start of the track; null for untimed lyrics.
  time: number | null
  text: string
}

export interface Lyrics {
  timed: boolean
  lines: LyricLine[]
}

const TIMESTAMP = /\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]/g
const METADATA = /^\[[a-z]+:.*\]\s*$/i

export function parseLyrics(text: string, kind: 'lrc' | 'txt'): Lyrics {
  const raw = text.replace(/^\uFEFF/, '').split(/\r?\n/)
  if (kind === 'lrc') {
    const timed = parseTimed(raw)
    if (timed.length > 0) return { timed: true, lines: timed }
  }
  return { timed: false, lines: parseUntimed(raw) }
}

// Lines without a timestamp (metadata, credits, blank) are dropped.
// "[01:02.00][02:30.00]Chorus" yields one entry per timestamp.
function parseTimed(raw: string[]): LyricLine[] {
  const lines: LyricLine[] = []
  for (const line of raw) {
    TIMESTAMP.lastIndex = 0
    const times: number[] = []
    let match: RegExpExecArray | null
    let end = 0
    while ((match = TIMESTAMP.exec(line)) !== null && match.index === end) {
      const minutes = Number(match[1])
      const seconds = Number(match[2])
      const fraction = match[3] ? Number(`0.${match[3]}`) : 0
      times.push(minutes * 60 + seconds + fraction)
      end = TIMESTAMP.lastIndex
    }
    if (times.length === 0) continue
    const text = line.slice(end).trim()
    for (const time of times) lines.push({ time, text })
  }
  lines.sort((a, b) => a.time! - b.time!)
  return lines
}

// Plain text: keep single blank lines as stanza breaks, collapse runs.
function parseUntimed(raw: string[]): LyricLine[] {
  const lines: LyricLine[] = []
  for (const line of raw) {
    const text = line.trim()
    if (METADATA.test(text)) continue
    if (text === '' && (lines.length === 0 || lines[lines.length - 1]!.text === '')) continue
    lines.push({ time: null, text })
  }
  while (lines.length > 0 && lines[lines.length - 1]!.text === '') lines.pop()
  return lines
}

// Index of the line that is current at `time`, or -1 before the first line.
export function currentLine(lines: LyricLine[], time: number): number {
  let lo = 0
  let hi = lines.length - 1
  let result = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (lines[mid]!.time! <= time) {
      result = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return result
}
