import { formatBytes, type OfflineProgress } from './offline'
import type { PlayerState } from './player'
import type { Track, TrackSet } from './tracks'
import type { WakeLockStatus } from './wakelock'

export type OfflineView =
  | { kind: 'hidden' }
  | { kind: 'checking' }
  | { kind: 'ready'; usage: number | null }
  | { kind: 'missing'; count: number }
  | { kind: 'downloading'; progress: OfflineProgress }
  | { kind: 'error'; message: string }

export interface UIHandlers {
  onToggle: (track: Track) => void
  onStop: () => void
  onSelectSet: (setId: string) => void
  onDownload: () => void
  onReload: () => void
}

type Attrs = Record<string, string | boolean | undefined>

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  children: (Node | string)[] = [],
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === false) continue
    if (key === 'class') node.className = String(value)
    else if (key === 'text') node.textContent = String(value)
    else node.setAttribute(key, value === true ? '' : String(value))
  }
  node.append(...children)
  return node
}

function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

export class UI {
  private readonly handlers: UIHandlers
  private readonly sets: TrackSet[]
  private readonly songButtons = new Map<string, HTMLButtonElement>()
  private readonly setButtons = new Map<string, HTMLButtonElement>()

  private readonly setsNav: HTMLElement
  private readonly songList: HTMLOListElement
  private readonly offlineChip: HTMLButtonElement
  private readonly wakeChip: HTMLSpanElement
  private readonly nowPlaying: HTMLElement
  private readonly nowTitle: HTMLElement
  private readonly nowSet: HTMLElement
  private readonly progressFill: HTMLElement
  private readonly elapsed: HTMLElement
  private readonly remaining: HTMLElement
  private readonly stopButton: HTMLButtonElement
  private readonly toast: HTMLElement

  private activeSetId: string | null = null
  private lastTrackId: string | null = null

  constructor(root: HTMLElement, sets: TrackSet[], handlers: UIHandlers) {
    this.sets = sets
    this.handlers = handlers

    this.offlineChip = el('button', { class: 'chip chip-offline', type: 'button', hidden: true })
    this.offlineChip.addEventListener('click', () => this.handlers.onDownload())
    this.wakeChip = el('span', { class: 'chip chip-wake', role: 'status' })

    this.setsNav = el('nav', { class: 'sets', 'aria-label': 'Sets' })
    this.songList = el('ol', { class: 'song-list' })

    this.nowTitle = el('div', { class: 'now-title' })
    this.nowSet = el('div', { class: 'now-set' })
    this.progressFill = el('div', { class: 'progress-fill' })
    this.elapsed = el('span', { class: 'time-elapsed', text: '0:00' })
    this.remaining = el('span', { class: 'time-remaining', text: '0:00' })
    this.stopButton = el('button', { class: 'stop', type: 'button', text: 'Stop' })
    this.stopButton.addEventListener('click', () => this.handlers.onStop())

    this.nowPlaying = el('footer', { class: 'now-playing', hidden: true }, [
      el('div', { class: 'now-text' }, [this.nowTitle, this.nowSet]),
      el('div', { class: 'progress', role: 'progressbar' }, [this.progressFill]),
      el('div', { class: 'times' }, [this.elapsed, this.remaining]),
      this.stopButton,
    ])

    const reload = el('button', { class: 'toast-action', type: 'button', text: 'Reload' })
    reload.addEventListener('click', () => this.handlers.onReload())
    this.toast = el('div', { class: 'toast', hidden: true }, [
      el('span', { text: 'Update available' }),
      reload,
    ])

    root.replaceChildren(
      el('header', { class: 'topbar' }, [
        el('h1', { class: 'brand', text: 'Band Companion' }),
        el('div', { class: 'chips' }, [this.offlineChip, this.wakeChip]),
      ]),
      this.setsNav,
      el('main', { class: 'songs' }, [this.songList]),
      this.nowPlaying,
      this.toast,
    )

    this.renderSets()
    const first = sets[0]
    if (first) this.selectSet(first.id)
    else this.renderEmpty()
  }

  selectSet(setId: string): void {
    if (this.activeSetId === setId) return
    this.activeSetId = setId
    for (const [id, button] of this.setButtons) {
      button.classList.toggle('is-active', id === setId)
      button.setAttribute('aria-pressed', String(id === setId))
    }
    this.renderSongs()
  }

  updatePlayer(state: PlayerState): void {
    const active = state.status === 'playing' || state.status === 'loading'
    const trackId = state.track?.id ?? null

    if (this.lastTrackId !== trackId || !active) {
      for (const [id, button] of this.songButtons) {
        const isCurrent = active && id === trackId
        button.classList.toggle('is-playing', isCurrent)
        button.setAttribute('aria-pressed', String(isCurrent))
      }
      this.lastTrackId = active ? trackId : null
    }

    if (state.status === 'error' && state.track) {
      const button = this.songButtons.get(state.track.id)
      button?.classList.add('is-error')
      const label = button?.querySelector('.song-state')
      if (label) label.textContent = state.error ?? 'Error'
    } else {
      for (const button of this.songButtons.values()) {
        if (!button.classList.contains('is-error')) continue
        button.classList.remove('is-error')
        const label = button.querySelector('.song-state')
        if (label) label.textContent = ''
      }
    }

    this.nowPlaying.hidden = !active
    if (!active || !state.track) return

    this.nowTitle.textContent = state.track.title
    this.nowSet.textContent = this.sets.length > 1 ? state.track.setName : ''
    const ratio = state.duration > 0 ? Math.min(1, state.currentTime / state.duration) : 0
    this.progressFill.style.transform = `scaleX(${ratio})`
    this.elapsed.textContent = formatTime(state.currentTime)
    this.remaining.textContent =
      state.duration > 0 ? `\u2212${formatTime(state.duration - state.currentTime)}` : '\u2013:\u2013\u2013'
    this.stopButton.textContent = state.status === 'loading' ? 'Loading\u2026' : 'Stop'
  }

  updateWake(status: WakeLockStatus): void {
    const chip = this.wakeChip
    chip.classList.remove('is-ok', 'is-warn')
    switch (status) {
      case 'active':
        chip.textContent = 'Awake'
        chip.classList.add('is-ok')
        break
      case 'inactive':
        chip.textContent = 'Will sleep'
        break
      case 'denied':
      case 'unsupported':
        chip.textContent = 'Can\u2019t keep awake'
        chip.classList.add('is-warn')
        break
    }
  }

  updateOffline(view: OfflineView): void {
    const chip = this.offlineChip
    chip.hidden = view.kind === 'hidden'
    chip.classList.remove('is-ok', 'is-warn', 'is-busy')
    chip.disabled = false
    switch (view.kind) {
      case 'hidden':
        break
      case 'checking':
        chip.textContent = 'Checking\u2026'
        chip.disabled = true
        break
      case 'ready':
        chip.textContent = view.usage ? `Offline \u00b7 ${formatBytes(view.usage)}` : 'Offline ready'
        chip.classList.add('is-ok')
        break
      case 'missing':
        chip.textContent = `Download ${view.count} ${view.count === 1 ? 'track' : 'tracks'}`
        break
      case 'downloading': {
        const { done, total } = view.progress
        chip.textContent = `Downloading ${done}/${total}`
        chip.classList.add('is-busy')
        chip.disabled = true
        break
      }
      case 'error':
        chip.textContent = `Retry \u00b7 ${view.message}`
        chip.classList.add('is-warn')
        chip.title = view.message
        break
    }
  }

  showUpdateAvailable(): void {
    this.toast.hidden = false
  }

  hideUpdateAvailable(): void {
    this.toast.hidden = true
  }

  private renderSets(): void {
    this.setButtons.clear()
    this.setsNav.replaceChildren()
    this.setsNav.hidden = this.sets.length < 2
    for (const set of this.sets) {
      const button = el('button', { class: 'pill', type: 'button', text: set.name, 'aria-pressed': 'false' })
      button.addEventListener('click', () => this.handlers.onSelectSet(set.id))
      this.setButtons.set(set.id, button)
      this.setsNav.append(button)
    }
  }

  private renderSongs(): void {
    const set = this.sets.find((s) => s.id === this.activeSetId)
    this.songButtons.clear()
    this.songList.replaceChildren()
    if (!set) return

    set.tracks.forEach((track, index) => {
      const button = el('button', { class: 'song', type: 'button', 'aria-pressed': 'false' }, [
        el('span', { class: 'song-index', text: String(index + 1) }),
        el('span', { class: 'song-title', text: track.title }),
        el('span', { class: 'song-state' }),
      ])
      button.addEventListener('click', () => this.handlers.onToggle(track))
      this.songButtons.set(track.id, button)
      this.songList.append(el('li', {}, [button]))
    })
    this.songList.parentElement?.scrollTo({ top: 0 })
  }

  private renderEmpty(): void {
    this.songList.replaceChildren(
      el('li', { class: 'empty' }, [
        el('p', { text: 'No tracks yet.' }),
        el('p', { class: 'empty-hint' }, [
          'Add audio files to ',
          el('code', { text: 'tracks/<set name>/' }),
          ' and deploy.',
        ]),
      ]),
    )
  }
}
