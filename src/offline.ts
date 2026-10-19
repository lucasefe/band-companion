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
  const wanted = new Set(tracks.map((t) => new URL(t.url, location.href).href))

  // Drop entries for tracks that no longer exist (renamed, re-encoded, removed).
  for (const req of await cache.keys()) {
    if (!wanted.has(req.url)) await cache.delete(req)
  }

  const missing: Track[] = []
  for (const track of tracks) {
    if (!(await cache.match(track.url))) missing.push(track)
  }

  let done = tracks.length - missing.length
  onProgress({ done, total: tracks.length, current: null })

  for (const track of missing) {
    signal?.throwIfAborted()
    onProgress({ done, total: tracks.length, current: track })
    const res = await fetch(track.url, { signal, cache: 'no-store' })
    if (!res.ok) throw new Error(`Failed to download “${track.title}” (${res.status})`)
    await cache.put(track.url, res)
    done += 1
    onProgress({ done, total: tracks.length, current: null })
  }

  if (navigator.storage?.persist) {
    try {
      await navigator.storage.persist()
    } catch {
      // Persistence is a hint; nothing to do if it fails.
    }
  }
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
