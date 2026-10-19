import { registerSW } from 'virtual:pwa-register'
import './styles.css'
import { FollowUI } from './follow-ui'
import { Monitor } from './monitor'
import { cachedTrackIds, downloadTracks, offlineSupported, storageUsage } from './offline'
import { Player } from './player'
import { leaderKey, setLeaderKey } from './role'
import { Broadcaster, Follower } from './sync'
import { allTracks, loadSets } from './tracks'
import { UI } from './ui'
import { keepAwake, subscribeWakeLock } from './wakelock'

const root = document.getElementById('app')!
const audioEl = document.getElementById('audio') as HTMLAudioElement
const monitorEl = document.getElementById('monitor') as HTMLAudioElement

const sets = loadSets()
const tracks = allTracks(sets)
const player = new Player(audioEl)

let updateSW: ((reload?: boolean) => Promise<void>) | null = null
let updateAvailable = false

interface Mounted {
  showUpdateAvailable: () => void
  showNotice: (text: string) => void
  destroy: () => void
}

let mounted: Mounted | null = null
// Shown by the next mount, e.g. "Wrong key" after dropping back to follower.
let pendingNotice: string | null = null

function mount(): void {
  mounted?.destroy()
  const key = leaderKey()
  mounted = key ? mountLeader(key) : mountFollower()
  if (updateAvailable) mounted.showUpdateAvailable()
  if (pendingNotice) {
    mounted.showNotice(pendingNotice)
    pendingNotice = null
  }
}

function mountLeader(key: string): Mounted {
  let downloading = false

  const ui = new UI(root, sets, {
    onSelect(track) {
      keepAwake()
      player.select(track)
    },
    onPlay() {
      keepAwake()
      player.play()
    },
    onPause() {
      player.pause()
    },
    onRestart() {
      player.restart()
    },
    onSeek(seconds) {
      player.seek(seconds)
    },
    onSelectSet(setId) {
      ui.selectSet(setId)
    },
    onDownload() {
      void downloadAll()
    },
    onReload() {
      void updateSW?.(true)
    },
    onStepDown() {
      setLeaderKey(null)
      mount()
    },
    getTime() {
      return audioEl.currentTime
    },
  })

  const broadcaster = new Broadcaster(
    key,
    () => {
      const state = player.getState()
      return {
        trackId: state.track?.id ?? null,
        playing: state.status === 'playing' || state.status === 'loading',
        position: audioEl.currentTime,
      }
    },
    {
      onLink: (link) => ui.updateLink(link),
      onEnd: (reason) => {
        setLeaderKey(null)
        pendingNotice = reason === 'unauthorized' ? 'Wrong key' : 'Another device took the lead'
        mount()
      },
    },
  )

  player.attachMediaSession()
  const unsubscribePlayer = player.subscribe((state) => {
    ui.updatePlayer(state)
    broadcaster.sync()
  })
  const unsubscribeWake = subscribeWakeLock((status) => ui.updateWake(status))

  async function refreshOfflineStatus(): Promise<void> {
    if (!offlineSupported || tracks.length === 0) {
      ui.updateOffline({ kind: 'hidden' })
      return
    }
    ui.updateOffline({ kind: 'checking' })
    const cached = await cachedTrackIds(tracks)
    const missing = tracks.length - cached.size
    if (missing === 0) ui.updateOffline({ kind: 'ready', usage: await storageUsage() })
    else ui.updateOffline({ kind: 'missing', count: missing })
  }

  async function downloadAll(): Promise<void> {
    if (downloading) return
    downloading = true
    try {
      await downloadTracks(tracks, (progress) => ui.updateOffline({ kind: 'downloading', progress }))
      await refreshOfflineStatus()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Download failed'
      ui.updateOffline({ kind: 'error', message: navigator.onLine ? message : 'Offline' })
    } finally {
      downloading = false
    }
  }

  void refreshOfflineStatus()

  return {
    showUpdateAvailable: () => ui.showUpdateAvailable(),
    showNotice: (text) => ui.showNotice(text),
    destroy() {
      player.pause()
      broadcaster.close()
      unsubscribePlayer()
      unsubscribeWake()
      ui.destroy()
    },
  }
}

function mountFollower(): Mounted {
  const ui = new FollowUI(root, sets, {
    onLead(key) {
      setLeaderKey(key)
      mount()
    },
    onListen() {
      keepAwake()
      monitor.toggle()
    },
    onReload() {
      void updateSW?.(true)
    },
    onTap() {
      keepAwake()
    },
  })
  const monitor = new Monitor(monitorEl, sets, (status) => ui.updateListen(status))
  const clock = (): number => follower.now()
  const follower = new Follower((view) => {
    ui.update(view, clock)
    monitor.follow(view, clock)
  })
  const unsubscribeWake = subscribeWakeLock((status) => ui.updateWake(status))

  return {
    showUpdateAvailable: () => ui.showUpdateAvailable(),
    showNotice: (text) => ui.showNotice(text),
    destroy() {
      follower.close()
      monitor.destroy()
      unsubscribeWake()
      ui.destroy()
    },
  }
}

mount()

if ('serviceWorker' in navigator) {
  updateSW = registerSW({
    onNeedRefresh() {
      updateAvailable = true
      mounted?.showUpdateAvailable()
    },
  })
}
