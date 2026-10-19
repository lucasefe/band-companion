export type WakeLockStatus = 'unsupported' | 'inactive' | 'active' | 'denied'

type Listener = (status: WakeLockStatus) => void

let sentinel: WakeLockSentinel | null = null
let wanted = false
let status: WakeLockStatus = 'wakeLock' in navigator ? 'inactive' : 'unsupported'
const listeners = new Set<Listener>()

function setStatus(next: WakeLockStatus): void {
  if (status === next) return
  status = next
  for (const listener of listeners) listener(status)
}

async function acquire(): Promise<void> {
  if (status === 'unsupported' || sentinel || document.visibilityState !== 'visible') return
  try {
    sentinel = await navigator.wakeLock.request('screen')
    sentinel.addEventListener('release', () => {
      sentinel = null
      setStatus('inactive')
    })
    setStatus('active')
  } catch {
    setStatus('denied')
  }
}

// Call from a user gesture. The lock is re-acquired automatically whenever the
// page becomes visible again (the browser releases it when the tab is hidden).
export function keepAwake(): void {
  wanted = true
  void acquire()
}

export function subscribeWakeLock(listener: Listener): () => void {
  listeners.add(listener)
  listener(status)
  return () => listeners.delete(listener)
}

document.addEventListener('visibilitychange', () => {
  if (wanted && document.visibilityState === 'visible') void acquire()
})
