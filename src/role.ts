// The device is the leader while it holds a key; otherwise it follows.
// Kept from the Band Companion days: changing it would sign every leader device out.
const STORAGE_KEY = 'band-companion:leader-key'

export function leaderKey(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function setLeaderKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(STORAGE_KEY, key)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Private mode without storage: the role lasts for this page load only.
  }
}
