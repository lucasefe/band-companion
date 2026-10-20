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
const BUCKET = 'band-companion-audio' // the deployed bucket; renaming it means re-uploading everything
const AUDIO_EXT = new Set(['.wav'])

const target = process.argv.includes('--local') ? 'local' : 'remote'

// Keys are NFC: macOS reports NFD folder names while git (core.precomposeunicode)
// checks them out as NFC, and the page matches lyrics by the same key.
const previous = Object.fromEntries(
  Object.entries(existsSync(MANIFEST) ? JSON.parse(readFileSync(MANIFEST, 'utf8')) : {}).map(([k, v]) => [
    k.normalize('NFC'),
    v,
  ]),
)
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

// A song folder holds the track the band plays along to (`<Title>.wav`) and, optionally,
// the original song as `<Title>.reference.wav` for the leader to mix in.
const folders = new Map()
for (const source of sources) {
  const folder = relative(ROOT, dirname(source)).normalize('NFC')
  const role = basename(source).toLowerCase().endsWith('.reference.wav') ? 'reference' : 'main'
  const roles = folders.get(folder) ?? {}
  if (roles[role]) {
    console.error(`  more than one ${role} audio file in ${folder}; using ${basename(roles[role])}`)
    continue
  }
  roles[role] = source
  folders.set(folder, roles)
}

for (const [folder, roles] of folders) {
  if (!roles.main) {
    console.error(`  ${folder} has a reference but no main audio file; skipped`)
    continue
  }
  const entry = await sync(folder, roles.main, previous[folder])
  if (roles.reference) entry.reference = await sync(`${folder} (reference)`, roles.reference, previous[folder]?.reference)
  manifest[folder] = entry
}

async function sync(label, source, before) {
  const hash = await hashFile(source)
  const key = `${hash}.wav`
  const bytes = statSync(source).size
  const alreadyUploaded = before?.key === key && before.uploaded?.includes(target)
  const entry = { key, bytes, source: basename(source), uploaded: before?.key === key ? (before.uploaded ?? []) : [] }

  if (alreadyUploaded) {
    console.log(`  skip  ${label}`)
    skipped += 1
    return entry
  }

  process.stdout.write(`  put   ${label} (${(bytes / (1024 * 1024)).toFixed(1)} MB) ... `)
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
  return entry
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
