// Cloudflare Worker: streams audio from R2, routes /ws to the Room Durable
// Object and falls through to static assets.
// Audio keys are content hashes (see scripts/sync-audio.mjs), so responses are immutable.

export { Room } from './room'

const AUDIO_PREFIX = '/audio/'
const KEY_PATTERN = /^[a-f0-9]{16}\.wav$/

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url)

    if (url.pathname.startsWith(AUDIO_PREFIX)) return serveAudio(request, env, url.pathname.slice(AUDIO_PREFIX.length))

    if (url.pathname === '/ws') {
      if (request.headers.get('Upgrade') !== 'websocket') return new Response('Expected WebSocket', { status: 426 })
      return env.ROOM.get(env.ROOM.idFromName('main')).fetch(request)
    }

    return env.ASSETS.fetch(request)
  },
} satisfies ExportedHandler<Env>

async function serveAudio(request: Request, env: Env, key: string): Promise<Response> {
  if (request.method !== 'GET' && request.method !== 'HEAD') return new Response(null, { status: 405 })
  if (!KEY_PATTERN.test(key)) return new Response('Not found', { status: 404 })

  let object: R2ObjectBody | null
  try {
    object = await env.AUDIO.get(key, { range: request.headers })
  } catch {
    // R2 rejects unsatisfiable ranges.
    return new Response(null, { status: 416 })
  }
  if (!object) return new Response('Not found', { status: 404 })

  const headers = new Headers({
    'Content-Type': 'audio/wav',
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'public, max-age=31536000, immutable',
    ETag: object.httpEtag,
  })
  const body = request.method === 'HEAD' ? null : object.body

  const range = resolveRange(object.range, object.size)
  if (range && request.headers.has('range')) {
    if (range.length <= 0 || range.offset >= object.size) {
      return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${object.size}` } })
    }
    headers.set('Content-Range', `bytes ${range.offset}-${range.offset + range.length - 1}/${object.size}`)
    headers.set('Content-Length', String(range.length))
    return new Response(body, { status: 206, headers })
  }

  headers.set('Content-Length', String(object.size))
  return new Response(body, { status: 200, headers })
}

// R2 reports the range it actually returned. Properties are checked by type
// because the local runtime leaves unused ones present but undefined.
function resolveRange(range: R2Range | undefined, size: number): { offset: number; length: number } | null {
  if (!range) return null
  const r = range as { offset?: number; length?: number; suffix?: number }
  if (typeof r.suffix === 'number') {
    const length = Math.min(r.suffix, size)
    return { offset: size - length, length }
  }
  const offset = typeof r.offset === 'number' ? r.offset : 0
  const length = typeof r.length === 'number' ? r.length : size - offset
  return { offset, length }
}
