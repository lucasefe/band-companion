import type { FollowerView } from './sync'
import { findTrack, type Track, type TrackSet } from './tracks'

// 'blocked': the browser refused to start playback outside a tap.
export type MonitorStatus = 'off' | 'on' | 'blocked'

// Seek when the local audio is this far from the leader's position.
const DRIFT = 0.5

// Follower-side audio that mirrors the leader: same track, same position, no
// transport. Off by default; nothing is loaded until the user turns it on.
export class Monitor {
  private readonly audio: HTMLAudioElement
  private readonly sets: TrackSet[]
  private readonly onStatus: (status: MonitorStatus) => void

  private status: MonitorStatus = 'off'
  private track: Track | null = null
  private view: FollowerView | null = null
  private clock: (() => number) | null = null

  constructor(audio: HTMLAudioElement, sets: TrackSet[], onStatus: (status: MonitorStatus) => void) {
    this.audio = audio
    this.sets = sets
    this.onStatus = onStatus
    // currentTime set before metadata arrives is unreliable on Safari; apply again once it has.
    audio.addEventListener('loadedmetadata', this.resync)
    this.attachMediaSession()
    onStatus(this.status)
  }

  // Call from a tap: iOS only lets an element start inside a user gesture.
  toggle(): void {
    if (this.status === 'on') this.turnOff()
    else this.turnOn()
  }

  follow(view: FollowerView, clock: () => number): void {
    this.view = view
    this.clock = clock
    this.apply(false)
  }

  destroy(): void {
    this.audio.removeEventListener('loadedmetadata', this.resync)
    this.unload()
  }

  private turnOn(): void {
    this.setStatus('on')
    this.apply(true)
  }

  private turnOff(): void {
    this.setStatus('off')
    this.audio.pause()
    this.setPlaybackState('paused')
  }

  private resync = (): void => this.apply(false)

  private apply(gesture: boolean): void {
    if (this.status === 'off') return
    const audio = this.audio
    const state = this.view?.state ?? null
    const track = state?.trackId ? findTrack(this.sets, state.trackId) : null

    if (!state || !track) {
      this.unload()
      if (gesture) this.unlock()
      return
    }

    if (track !== this.track) {
      this.track = track
      audio.src = track.url
      audio.load()
      this.updateMetadata(track)
    }

    if (state.playing) {
      const target = this.clock?.() ?? state.position
      if (Math.abs(audio.currentTime - target) > DRIFT) audio.currentTime = target
      if (audio.paused) this.play()
      return
    }

    if (!audio.paused) audio.pause()
    if (Math.abs(audio.currentTime - state.position) > DRIFT) audio.currentTime = state.position
    this.setPlaybackState('paused')
    if (gesture) this.unlock()
  }

  private play(): void {
    this.audio.play().then(
      () => {
        if (this.status === 'blocked') this.setStatus('on')
        this.setPlaybackState('playing')
      },
      (err: unknown) => {
        if (err instanceof DOMException && err.name === 'NotAllowedError') this.setStatus('blocked')
      },
    )
  }

  // play() inside the gesture marks the element as user-started, so later
  // programmatic play() calls are allowed; pausing right away keeps it silent.
  private unlock(): void {
    this.audio.play().catch(() => {})
    this.audio.pause()
  }

  private unload(): void {
    this.track = null
    this.audio.pause()
    this.audio.removeAttribute('src')
    this.audio.load()
    this.setPlaybackState('none')
  }

  private setStatus(status: MonitorStatus): void {
    if (this.status === status) return
    this.status = status
    this.onStatus(status)
  }

  // The lock screen shows the song; play/pause there turn listening on and off.
  private attachMediaSession(): void {
    if (!('mediaSession' in navigator)) return
    navigator.mediaSession.setActionHandler('play', () => this.turnOn())
    navigator.mediaSession.setActionHandler('pause', () => this.turnOff())
    navigator.mediaSession.setActionHandler('stop', () => this.turnOff())
    navigator.mediaSession.setActionHandler('previoustrack', null)
    navigator.mediaSession.setActionHandler('nexttrack', null)
  }

  private updateMetadata(track: Track): void {
    if (!('mediaSession' in navigator)) return
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: `${track.artist ?? 'Backline'} \u00b7 following`,
      album: track.setName,
    })
  }

  private setPlaybackState(state: MediaSessionPlaybackState): void {
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = state
  }
}
