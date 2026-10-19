import { registerSW } from 'virtual:pwa-register'
import './styles.css'
import { cachedTrackIds, downloadTracks, offlineSupported, storageUsage } from './offline'
import { Player } from './player'
import { allTracks, loadSets } from './tracks'
import { UI } from './ui'
import { keepAwake, subscribeWakeLock } from './wakelock'

const root = document.getElementById('app')!
const audioEl = document.getElementById('audio') as HTMLAudioElement

const sets = loadSets()
const tracks = allTracks(sets)
const player = new Player(audioEl)

let updateSW: ((reload?: boolean) => Promise<void>) | null = null
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
  onSelectSet(setId) {
    ui.selectSet(setId)
  },
  onDownload() {
    void downloadAll()
  },
  onReload() {
    void updateSW?.(true)
  },
})

player.subscribe((state) => ui.updatePlayer(state))
subscribeWakeLock((status) => ui.updateWake(status))

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

if ('serviceWorker' in navigator) {
  updateSW = registerSW({
    onNeedRefresh() {
      ui.showUpdateAvailable()
    },
  })
}
