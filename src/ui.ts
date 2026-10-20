import { el, formatTime } from './dom'
import { LyricsView } from './lyrics-view'
import { formatBytes, type OfflineProgress } from './offline'
import type { PlayerState } from './player'
import type { ReferenceState } from './reference'
import type { LeaderLink } from './sync'
import { describe, type Track, type TrackSet } from './tracks'
import type { WakeLockStatus } from './wakelock'

export type OfflineView =
  | { kind: 'hidden' }
  | { kind: 'checking' }
  | { kind: 'ready'; usage: number | null }
  | { kind: 'missing'; count: number }
  | { kind: 'downloading'; progress: OfflineProgress }
  | { kind: 'error'; message: string }

export interface UIHandlers {
  onSelect: (track: Track) => void
  onPlay: () => void
  onPause: () => void
  onRestart: () => void
  onSeek: (seconds: number) => void
  onSelectSet: (setId: string) => void
  onDownload: () => void
  onReload: () => void
  onStepDown: () => void
  // Reference track (the original song under the drums), leader only.
  onReference: () => void
  onReferenceMute: (muted: boolean) => void
  onReferenceVolume: (volume: number) => void
  // Playback position in seconds, read every frame while playing.
  getTime: () => number
}

export type View = 'songs' | 'lyrics'

export class UI {
  private readonly handlers: UIHandlers
  private readonly sets: TrackSet[]
  private readonly songButtons = new Map<string, HTMLButtonElement>()
  private readonly setButtons = new Map<string, HTMLButtonElement>()

  private readonly root: HTMLElement
  private readonly setsNav: HTMLElement
  private readonly songList: HTMLOListElement
  private readonly songsButton: HTMLButtonElement
  private readonly lyrics: LyricsView
  private readonly offlineChip: HTMLButtonElement
  private readonly wakeChip: HTMLSpanElement
  private readonly liveChip: HTMLButtonElement
  private readonly notice: HTMLElement
  private readonly nowPlaying: HTMLElement
  private readonly nowTitle: HTMLElement
  private readonly nowSet: HTMLElement
  private readonly nowSource: HTMLAnchorElement
  private readonly seekBar: HTMLElement
  private readonly progressFill: HTMLElement
  private readonly elapsed: HTMLElement
  private readonly remaining: HTMLElement
  private readonly playButton: HTMLButtonElement
  private readonly pauseButton: HTMLButtonElement
  private readonly restartButton: HTMLButtonElement
  private readonly referenceRow: HTMLElement
  private readonly referenceChip: HTMLButtonElement
  private readonly referenceMute: HTMLButtonElement
  private readonly referenceVolume: HTMLInputElement
  private readonly toast: HTMLElement

  private activeSetId: string | null = null
  private lastTrackId: string | null = null
  private lastPlaying = false
  private duration = 0
  private scrubbing = false

  constructor(root: HTMLElement, sets: TrackSet[], handlers: UIHandlers) {
    this.root = root
    this.sets = sets
    this.handlers = handlers

    this.offlineChip = el('button', { class: 'chip chip-offline', type: 'button', hidden: true })
    this.offlineChip.addEventListener('click', () => this.handlers.onDownload())
    this.wakeChip = el('span', { class: 'chip chip-wake', role: 'status' })
    this.liveChip = el('button', { class: 'chip chip-live', type: 'button', text: 'Leader' })
    this.liveChip.addEventListener('click', () => {
      if (confirm('Stop leading? Followers will keep the last position until someone else leads.')) {
        this.handlers.onStepDown()
      }
    })
    this.notice = el('div', { class: 'toast toast-notice', role: 'status', hidden: true })

    this.songsButton = el('button', { class: 'pill songs-back', type: 'button', text: '\u2039 Songs' })
    this.songsButton.addEventListener('click', () => this.showView('songs'))

    this.setsNav = el('nav', { class: 'sets', 'aria-label': 'Sets' })
    this.songList = el('ol', { class: 'song-list' })
    this.lyrics = new LyricsView({ onSeek: (time) => this.handlers.onSeek(time) })

    this.nowTitle = el('div', { class: 'now-title' })
    this.nowSet = el('div', { class: 'now-set' })
    this.nowSource = el('a', { class: 'now-source', target: '_blank', rel: 'noopener', hidden: true })
    this.progressFill = el('div', { class: 'progress-fill' })
    this.seekBar = el('div', { class: 'seek', role: 'slider', tabindex: '0', 'aria-label': 'Position', 'aria-valuemin': '0' }, [
      el('div', { class: 'progress' }, [this.progressFill]),
    ])
    this.attachSeek()
    this.elapsed = el('span', { class: 'time-elapsed', text: '0:00' })
    this.remaining = el('span', { class: 'time-remaining', text: '0:00' })

    this.restartButton = el('button', { class: 'transport transport-restart', type: 'button', text: 'Restart' })
    this.playButton = el('button', { class: 'transport transport-play', type: 'button', text: 'Play' })
    this.pauseButton = el('button', { class: 'transport transport-pause', type: 'button', text: 'Pause' })
    this.restartButton.addEventListener('click', () => this.handlers.onRestart())
    this.playButton.addEventListener('click', () => this.handlers.onPlay())
    this.pauseButton.addEventListener('click', () => this.handlers.onPause())

    this.referenceChip = el('button', { class: 'chip chip-reference', type: 'button', text: 'Reference' })
    this.referenceMute = el('button', { class: 'pill reference-mute', type: 'button', text: 'Mute' })
    this.referenceVolume = el('input', {
      class: 'reference-volume',
      type: 'range',
      min: '0',
      max: '100',
      step: '1',
      'aria-label': 'Reference volume',
    }) as HTMLInputElement
    this.referenceChip.addEventListener('click', () => this.handlers.onReference())
    this.referenceMute.addEventListener('click', () =>
      this.handlers.onReferenceMute(this.referenceMute.getAttribute('aria-pressed') !== 'true'),
    )
    this.referenceVolume.addEventListener('input', () =>
      this.handlers.onReferenceVolume(Number(this.referenceVolume.value) / 100),
    )
    this.referenceRow = el('div', { class: 'reference-row', hidden: true }, [
      this.referenceChip,
      this.referenceMute,
      this.referenceVolume,
    ])

    this.nowPlaying = el('footer', { class: 'now-playing', hidden: true }, [
      el('div', { class: 'now-text' }, [
        this.nowTitle,
        el('div', { class: 'now-meta' }, [this.nowSet, this.nowSource]),
      ]),
      this.seekBar,
      el('div', { class: 'times' }, [this.elapsed, this.remaining]),
      this.referenceRow,
      el('div', { class: 'transport-row' }, [this.restartButton, this.playButton, this.pauseButton]),
    ])

    const reload = el('button', { class: 'toast-action', type: 'button', text: 'Reload' })
    reload.addEventListener('click', () => this.handlers.onReload())
    this.toast = el('div', { class: 'toast', hidden: true }, [
      el('span', { text: 'Update available' }),
      reload,
    ])

    root.replaceChildren(
      el('header', { class: 'topbar' }, [
        el('h1', { class: 'brand', text: 'Backline' }),
        this.songsButton,
        el('div', { class: 'chips' }, [this.offlineChip, this.wakeChip, this.liveChip]),
      ]),
      this.setsNav,
      el('main', { class: 'songs' }, [this.songList]),
      this.lyrics.element,
      this.nowPlaying,
      this.toast,
      this.notice,
    )
    root.classList.remove('follow')
    this.showView('songs')

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

  // On phones the two views alternate; wider screens show both (see styles.css).
  showView(view: View): void {
    this.root.classList.toggle('view-songs', view === 'songs')
    this.root.classList.toggle('view-lyrics', view === 'lyrics')
  }

  updatePlayer(state: PlayerState): void {
    const selected = state.track !== null && state.status !== 'idle'
    const playing = state.status === 'playing' || state.status === 'loading'
    const trackId = selected ? state.track!.id : null

    if (this.lastTrackId !== trackId) {
      const track = selected ? state.track : null
      this.lyrics.setLyrics(track?.lyrics ?? null, track ? undefined : 'Tap a song')
    }
    if (playing) this.lyrics.start(this.handlers.getTime)
    else {
      this.lyrics.stop()
      this.lyrics.update(state.currentTime)
    }

    if (this.lastTrackId !== trackId || this.lastPlaying !== playing) {
      for (const [id, button] of this.songButtons) {
        const isSelected = id === trackId
        button.classList.toggle('is-selected', isSelected)
        button.classList.toggle('is-playing', isSelected && playing)
        button.setAttribute('aria-pressed', String(isSelected))
        const label = button.querySelector('.song-state')
        if (label && !button.classList.contains('is-error')) {
          label.textContent = isSelected ? (playing ? '' : 'Loaded') : ''
        }
      }
      this.lastTrackId = trackId
      this.lastPlaying = playing
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

    this.nowPlaying.hidden = !selected
    if (!selected || !state.track) return

    this.nowTitle.textContent = state.track.title
    this.nowSet.textContent = describe(state.track, this.sets.length > 1)
    this.nowSource.hidden = !state.track.source
    if (state.track.source) {
      this.nowSource.href = state.track.source.url
      this.nowSource.textContent = `${state.track.source.label} \u2197`
    }
    this.duration = state.duration
    this.seekBar.setAttribute('aria-valuemax', String(Math.round(state.duration)))
    // While dragging, the bar previews the drag position instead of the player's.
    if (!this.scrubbing) this.renderPosition(state.currentTime)

    this.playButton.disabled = playing
    this.pauseButton.disabled = !playing
    this.playButton.textContent = state.status === 'loading' ? 'Loading\u2026' : 'Play'
    this.nowPlaying.classList.toggle('is-playing', playing)
  }

  updateReference(state: ReferenceState): void {
    this.referenceRow.hidden = !state.available
    const chip = this.referenceChip
    chip.classList.remove('is-ok', 'is-warn')
    chip.setAttribute('aria-pressed', String(state.status !== 'off'))
    switch (state.status) {
      case 'off':
        chip.textContent = 'Reference off'
        break
      case 'on':
        chip.textContent = 'Reference'
        chip.classList.add('is-ok')
        break
      case 'blocked':
        chip.textContent = 'Tap to hear reference'
        chip.classList.add('is-warn')
        break
      case 'error':
        chip.textContent = 'Reference failed'
        chip.classList.add('is-warn')
        break
    }
    const on = state.status !== 'off'
    this.referenceMute.disabled = !on
    this.referenceMute.textContent = state.muted ? 'Unmute' : 'Mute'
    this.referenceMute.setAttribute('aria-pressed', String(state.muted))
    this.referenceMute.classList.toggle('is-active', state.muted)
    this.referenceVolume.disabled = !on
    const percent = String(Math.round(state.volume * 100))
    if (this.referenceVolume.value !== percent) this.referenceVolume.value = percent
    this.referenceVolume.setAttribute('aria-valuetext', `${percent}%`)
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

  updateLink(link: LeaderLink): void {
    const chip = this.liveChip
    chip.classList.remove('is-ok', 'is-warn', 'is-busy')
    switch (link.status) {
      case 'live':
        chip.textContent = `Leader \u00b7 ${link.followers}`
        chip.classList.add('is-ok')
        break
      case 'connecting':
        chip.textContent = 'Leader \u00b7 connecting\u2026'
        chip.classList.add('is-busy')
        break
      case 'offline':
        chip.textContent = 'Leader \u00b7 offline'
        chip.classList.add('is-warn')
        break
    }
  }

  showNotice(text: string): void {
    this.notice.textContent = text
    this.notice.hidden = false
    window.setTimeout(() => (this.notice.hidden = true), 4000)
  }

  showUpdateAvailable(): void {
    this.toast.hidden = false
  }

  hideUpdateAvailable(): void {
    this.toast.hidden = true
  }

  destroy(): void {
    this.lyrics.stop()
  }

  private renderPosition(time: number): void {
    const ratio = this.duration > 0 ? Math.min(1, time / this.duration) : 0
    this.progressFill.style.transform = `scaleX(${ratio})`
    this.seekBar.setAttribute('aria-valuenow', String(Math.round(time)))
    this.elapsed.textContent = formatTime(time)
    this.remaining.textContent = this.duration > 0 ? `\u2212${formatTime(this.duration - time)}` : '\u2013:\u2013\u2013'
  }

  // Tap or drag to seek. The seek is committed on release; the bar previews until then.
  private attachSeek(): void {
    const bar = this.seekBar
    const timeAt = (event: PointerEvent): number => {
      const rect = bar.getBoundingClientRect()
      const ratio = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0
      return Math.min(1, Math.max(0, ratio)) * this.duration
    }
    const finish = (event: PointerEvent) => {
      if (!this.scrubbing) return
      this.scrubbing = false
      bar.classList.remove('is-scrubbing')
      this.handlers.onSeek(timeAt(event))
    }
    bar.addEventListener('pointerdown', (event) => {
      if (this.duration <= 0) return
      event.preventDefault()
      try {
        bar.setPointerCapture(event.pointerId)
      } catch {
        // Synthetic events have no active pointer; the drag still works within the bar.
      }
      this.scrubbing = true
      bar.classList.add('is-scrubbing')
      this.renderPosition(timeAt(event))
    })
    bar.addEventListener('pointermove', (event) => {
      if (this.scrubbing) this.renderPosition(timeAt(event))
    })
    bar.addEventListener('pointerup', finish)
    bar.addEventListener('pointercancel', finish)
    bar.addEventListener('keydown', (event) => {
      if (this.duration <= 0) return
      const step = event.key === 'ArrowRight' ? 5 : event.key === 'ArrowLeft' ? -5 : 0
      if (step === 0) return
      event.preventDefault()
      const current = Number(bar.getAttribute('aria-valuenow') ?? 0)
      this.handlers.onSeek(Math.min(this.duration, Math.max(0, current + step)))
    })
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
      const text = [el('span', { class: 'song-title', text: track.title })]
      if (track.artist) text.push(el('span', { class: 'song-artist', text: track.artist }))
      const button = el('button', { class: 'song', type: 'button', 'aria-pressed': 'false' }, [
        el('span', { class: 'song-index', text: String(index + 1) }),
        el('span', { class: 'song-text' }, text),
        el('span', { class: 'song-state' }),
      ])
      button.addEventListener('click', () => {
        this.handlers.onSelect(track)
        if (track.lyrics) this.showView('lyrics')
      })
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
