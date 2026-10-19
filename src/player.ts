import type { Track } from './tracks'

export type PlayerStatus = 'idle' | 'paused' | 'loading' | 'playing' | 'error'

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
    audio.addEventListener('pause', () => {
      if (this.state.status === 'playing' || this.state.status === 'loading') this.patch({ status: 'paused' })
    })
    audio.addEventListener('waiting', () => {
      if (this.state.status === 'playing') this.patch({ status: 'loading' })
    })
    audio.addEventListener('timeupdate', () => this.syncTime())
    audio.addEventListener('durationchange', () => this.syncTime())
    audio.addEventListener('ended', () => {
      audio.currentTime = 0
      this.patch({ status: 'paused', currentTime: 0 })
      this.setPlaybackState('paused')
    })
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
      navigator.mediaSession.setActionHandler('play', () => this.play())
      navigator.mediaSession.setActionHandler('pause', () => this.pause())
      navigator.mediaSession.setActionHandler('stop', () => this.pause())
      navigator.mediaSession.setActionHandler('previoustrack', () => this.restart())
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

  // Loads the track without playing it. Selecting the current track is a no-op.
  select(track: Track): void {
    if (this.state.track?.id === track.id && this.state.status !== 'error') return
    this.audio.pause()
    this.audio.src = track.url
    this.audio.load()
    this.patch({ status: 'paused', track, currentTime: 0, duration: 0, error: null })
    this.updateMediaSession(track)
  }

  play(): void {
    if (!this.state.track || this.state.status === 'playing' || this.state.status === 'loading') return
    this.patch({ status: 'loading', error: null })
    this.setPlaybackState('playing')
    // play() must be called synchronously from the user gesture for iOS.
    this.audio.play().catch((err: unknown) => {
      if (err instanceof DOMException && err.name === 'AbortError') return
      this.patch({ status: 'error', error: 'Tap Play again' })
    })
  }

  pause(): void {
    if (!this.state.track) return
    this.audio.pause()
    this.patch({ status: 'paused' })
    this.setPlaybackState('paused')
  }

  // Rewinds to the start and keeps the current playing/paused state.
  restart(): void {
    if (!this.state.track) return
    this.audio.currentTime = 0
    this.patch({ currentTime: 0 })
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
    this.setPlaybackState('paused')
  }

  private setPlaybackState(state: MediaSessionPlaybackState): void {
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = state
  }

  private patch(partial: Partial<PlayerState>): void {
    this.state = { ...this.state, ...partial }
    for (const listener of this.listeners) listener(this.state)
  }
}
