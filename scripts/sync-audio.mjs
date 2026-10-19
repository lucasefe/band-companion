// Uploads audio in tracks/ to R2 and writes tracks/audio.json, which the app
// reads at build time. Keys are content hashes, so a re-exported file gets a
// new URL and the offline cache notices.
//
// Usage:
//   npm run sync-audio             # upload to the real bucket (needs `wrangler login`)
//   npm run sync-audio -- --local  # upload to the local bucket used by `wrangler dev`
//
// Lossless sources are git-ignored, so audio.json is committed: a build on a
// machine without the audio files still knows every track.
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { createReadStream, existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { basename, dirname, extname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('../tracks/', import.meta.url))
const MANIFEST = join(ROOT, 'audio.json')
const BUCKET = 'band-companion-audio'
const AUDIO_EXT = new Set(['.wav'])

const target = process.argv.includes('--local') ? 'local' : 'remote'

const previous = existsSync(MANIFEST) ? JSON.parse(readFileSync(MANIFEST, 'utf8')) : {}
const manifest = {}

const sources = readdirSync(ROOT, { recursive: true, withFileTypes: true })
  .filter((entry) => entry.isFile() && AUDIO_EXT.has(extname(entry.name).toLowerCase()))
  .map((entry) => join(entry.parentPath, entry.name))
  .sort()

if (sources.length === 0) {
  console.log('No audio files in tracks/, leaving audio.json untouched.')
  process.exit(0)
}

let uploaded = 0
let skipped = 0
let failed = 0

for (const source of sources) {
  const folder = relative(ROOT, dirname(source))
  if (folder in manifest) {
    console.error(`  more than one audio file in ${folder}; using ${basename(manifest[folder].source)}`)
    continue
  }

  const hash = await hashFile(source)
  const key = `${hash}.wav`
  const bytes = statSync(source).size
  const before = previous[folder]
  const alreadyUploaded = before?.key === key && before.uploaded?.includes(target)
  const entry = { key, bytes, source: basename(source), uploaded: before?.key === key ? (before.uploaded ?? []) : [] }
  manifest[folder] = entry

  if (alreadyUploaded) {
    console.log(`  skip  ${folder}`)
    skipped += 1
    continue
  }

  process.stdout.write(`  put   ${folder} (${(bytes / (1024 * 1024)).toFixed(1)} MB) ... `)
  const result = spawnSync(
    'npx',
    ['wrangler', 'r2', 'object', 'put', `${BUCKET}/${key}`, '--file', source, '--content-type', 'audio/wav', `--${target}`],
    { stdio: ['ignore', 'pipe', 'pipe'] },
  )
  if (result.status === 0) {
    console.log('ok')
    entry.uploaded = [...entry.uploaded, target]
    uploaded += 1
  } else {
    console.log('failed')
    console.error(result.stderr.toString().trim())
    failed += 1
  }
}

const sorted = Object.fromEntries(Object.entries(manifest).sort(([a], [b]) => a.localeCompare(b)))
writeFileSync(MANIFEST, JSON.stringify(sorted, null, 2) + '\n')

console.log(`\n${uploaded} uploaded, ${skipped} up to date, ${failed} failed (${target}).`)
if (failed > 0) process.exit(1)

function hashFile(path) {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256')
    createReadStream(path)
      .on('data', (chunk) => hash.update(chunk))
      .on('error', reject)
      .on('end', () => resolve(hash.digest('hex').slice(0, 16)))
  })
}
