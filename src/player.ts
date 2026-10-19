import type { Track } from './tracks'

export type PlayerStatus = 'idle' | 'loading' | 'playing' | 'error'

export interface PlayerState {
  status: PlayerStatus
  track: Track | null
  currentTime: number
  duration: number
  error: string | null
}

type Listener = (state: PlayerState) => void

export class Player {
  private readonly audio: HTMLAudioElement
  private readonly listeners = new Set<Listener>()
  private state: PlayerState = { status: 'idle', track: null, currentTime: 0, duration: 0, error: null }

  constructor(audio: HTMLAudioElement) {
    this.audio = audio

    audio.addEventListener('playing', () => this.patch({ status: 'playing', error: null }))
    audio.addEventListener('waiting', () => {
      if (this.state.status === 'playing') this.patch({ status: 'loading' })
    })
    audio.addEventListener('timeupdate', () => this.syncTime())
    audio.addEventListener('durationchange', () => this.syncTime())
    audio.addEventListener('ended', () => this.stop())
    audio.addEventListener('error', () => {
      if (!audio.getAttribute('src')) return
      const code = audio.error?.code
      const message =
        code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED
          ? 'Format not supported'
          : code === MediaError.MEDIA_ERR_NETWORK
            ? 'Network error'
            : 'Playback failed'
      this.patch({ status: 'error', error: message })
    })

    if ('mediaSession' in navigator) {
      navigator.mediaSession.setActionHandler('pause', () => this.stop())
      navigator.mediaSession.setActionHandler('stop', () => this.stop())
      navigator.mediaSession.setActionHandler('play', () => {
        if (this.state.track && this.state.status === 'idle') this.play(this.state.track)
      })
    }
  }

  getState(): PlayerState {
    return this.state
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    listener(this.state)
    return () => this.listeners.delete(listener)
  }

  toggle(track: Track): void {
    const isCurrent = this.state.track?.id === track.id
    const isActive = this.state.status === 'playing' || this.state.status === 'loading'
    if (isCurrent && isActive) this.stop()
    else this.play(track)
  }

  play(track: Track): void {
    this.audio.pause()
    this.audio.src = track.url
    this.patch({ status: 'loading', track, currentTime: 0, duration: 0, error: null })
    this.updateMediaSession(track)

    // play() must be called synchronously from the user gesture for iOS.
    this.audio.play().catch((err: unknown) => {
      if (err instanceof DOMException && err.name === 'AbortError') return
      this.patch({ status: 'error', error: 'Tap again to play' })
    })
  }

  stop(): void {
    this.audio.pause()
    this.audio.removeAttribute('src')
    this.audio.load()
    this.patch({ status: 'idle', currentTime: 0, duration: 0, error: null })
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'none'
  }

  private syncTime(): void {
    const duration = Number.isFinite(this.audio.duration) ? this.audio.duration : 0
    this.patch({ currentTime: this.audio.currentTime, duration })
  }

  private updateMediaSession(track: Track): void {
    if (!('mediaSession' in navigator)) return
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: 'Band Companion',
      album: track.setName,
    })
    navigator.mediaSession.playbackState = 'playing'
  }

  private patch(partial: Partial<PlayerState>): void {
    this.state = { ...this.state, ...partial }
    for (const listener of this.listeners) listener(this.state)
  }
}
