// Converts lossless audio in tracks/ to 320 kbps MP3 next to the source.
// Sources are skipped when an up-to-date MP3 already exists.
// Usage: npm run convert
import { spawnSync } from 'node:child_process'
import { readdirSync, statSync } from 'node:fs'
import { join, extname } from 'node:path'

const ROOT = new URL('../tracks/', import.meta.url).pathname
const SOURCE_EXT = new Set(['.wav', '.aif', '.aiff', '.flac'])
const BITRATE = '320k'

const sources = readdirSync(ROOT, { recursive: true, withFileTypes: true })
  .filter((entry) => entry.isFile() && SOURCE_EXT.has(extname(entry.name).toLowerCase()))
  .map((entry) => join(entry.parentPath, entry.name))
  .sort()

if (sources.length === 0) {
  console.log('No WAV/AIFF/FLAC files in tracks/, nothing to convert.')
  process.exit(0)
}

if (spawnSync('ffmpeg', ['-version'], { stdio: 'ignore' }).status !== 0) {
  console.error('ffmpeg is required: brew install ffmpeg')
  process.exit(1)
}

let converted = 0
let failed = 0

for (const source of sources) {
  const target = source.slice(0, -extname(source).length) + '.mp3'
  const name = source.slice(ROOT.length)

  try {
    if (statSync(target).mtimeMs >= statSync(source).mtimeMs) {
      console.log(`  skip  ${name}`)
      continue
    }
  } catch {
    // No MP3 yet.
  }

  process.stdout.write(`  conv  ${name} ... `)
  const result = spawnSync(
    'ffmpeg',
    ['-y', '-loglevel', 'error', '-i', source, '-codec:a', 'libmp3lame', '-b:a', BITRATE, '-map_metadata', '-1', target],
    { stdio: ['ignore', 'inherit', 'inherit'] },
  )
  if (result.status === 0) {
    const mb = (statSync(target).size / (1024 * 1024)).toFixed(1)
    console.log(`${mb} MB`)
    converted += 1
  } else {
    console.log('failed')
    failed += 1
  }
}

console.log(`\n${converted} converted, ${sources.length - converted - failed} up to date, ${failed} failed.`)
if (failed > 0) process.exit(1)
