import type { Track } from './tracks'

// Must match the cache name read by the service worker (src/sw.ts).
export const TRACK_CACHE = 'tracks-v1'

export interface OfflineProgress {
  done: number
  total: number
  current: Track | null
}

export const offlineSupported = typeof caches !== 'undefined'

export async function cachedTrackIds(tracks: Track[]): Promise<Set<string>> {
  const ids = new Set<string>()
  if (!offlineSupported) return ids
  const cache = await caches.open(TRACK_CACHE)
  const keys = await cache.keys()
  const cachedUrls = new Set(keys.map((req) => req.url))
  for (const track of tracks) {
    if (cachedUrls.has(new URL(track.url, location.href).href)) ids.add(track.id)
  }
  return ids
}

export async function downloadTracks(
  tracks: Track[],
  onProgress: (progress: OfflineProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  const cache = await caches.open(TRACK_CACHE)
  const files = tracks.map((track) => ({ url: track.url, track }))
  const wanted = new Set(files.map((f) => new URL(f.url, location.href).href))

  // Drop entries for tracks that no longer exist (renamed, re-encoded, removed).
  for (const req of await cache.keys()) {
    if (!wanted.has(req.url)) await cache.delete(req)
  }

  const missing: typeof files = []
  for (const file of files) {
    if (!(await cache.match(file.url))) missing.push(file)
  }

  let done = files.length - missing.length
  onProgress({ done, total: files.length, current: null })

  for (const { url, track } of missing) {
    signal?.throwIfAborted()
    onProgress({ done, total: files.length, current: track })
    const res = await fetch(url, { signal, cache: 'no-store' })
    if (!res.ok) throw new Error(`Failed to download “${track.title}” (${res.status})`)
    await cache.put(url, res)
    done += 1
    onProgress({ done, total: files.length, current: null })
  }

  if (navigator.storage?.persist) {
    try {
      await navigator.storage.persist()
    } catch {
      // Persistence is a hint; nothing to do if it fails.
    }
  }
}

// --- one track at a time: what the leader needs before it can play ----------------------

export interface FileProgress {
  loaded: number // bytes so far, across the files
  total: number // bytes expected, across the files (0 when unknown)
}

export async function isCached(url: string): Promise<boolean> {
  if (!offlineSupported) return false
  const cache = await caches.open(TRACK_CACHE)
  return (await cache.match(url)) !== undefined
}

// Download the given files into the cache if they are not there yet. Unlike downloadTracks()
// this never deletes anything, so songs can be fetched one by one.
export async function ensureCached(
  urls: string[],
  onProgress?: (progress: FileProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  const cache = await caches.open(TRACK_CACHE)
  const missing: string[] = []
  for (const url of urls) if (!(await cache.match(url))) missing.push(url)
  if (missing.length === 0) return

  const sizes = new Map<string, number>()
  let loaded = 0
  const report = (): void => onProgress?.({ loaded, total: [...sizes.values()].reduce((a, b) => a + b, 0) })

  for (const url of missing) {
    signal?.throwIfAborted()
    const res = await fetch(url, { signal, cache: 'no-store' })
    if (!res.ok || !res.body) throw new Error(`Download failed (${res.status})`)
    const expected = Number(res.headers.get('content-length') ?? 0)
    sizes.set(url, expected)
    report()
    const chunks: Uint8Array[] = []
    const reader = res.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      loaded += value.byteLength
      report()
    }
    const body = new Blob(chunks as BlobPart[], { type: res.headers.get('content-type') ?? 'audio/wav' })
    sizes.set(url, body.size)
    await cache.put(
      url,
      new Response(body, {
        status: 200,
        headers: {
          'content-type': body.type,
          'content-length': String(body.size),
          'accept-ranges': 'bytes',
        },
      }),
    )
    report()
  }

  if (navigator.storage?.persist) {
    try {
      await navigator.storage.persist()
    } catch {
      // Persistence is a hint; nothing to do if it fails.
    }
  }
}

// A blob: URL for a cached file, or null. Playing from a blob keeps the service worker — and
// its range slicing of 60 MB responses — out of the audio path entirely. Callers revoke it.
export async function cachedBlobUrl(url: string): Promise<string | null> {
  if (!offlineSupported) return null
  const cache = await caches.open(TRACK_CACHE)
  const res = await cache.match(url)
  if (!res) return null
  return URL.createObjectURL(await res.blob())
}

export async function storageUsage(): Promise<number | null> {
  if (!navigator.storage?.estimate) return null
  try {
    const { usage } = await navigator.storage.estimate()
    return usage ?? null
  } catch {
    return null
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(0)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}
