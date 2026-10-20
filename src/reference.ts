import type { Track } from './tracks'

// The original song, mixed under the drums on the leader device. It is a second <audio>
// element locked to the main one and routed through a Web Audio gain node, because iOS
// ignores HTMLMediaElement.volume. Off by default; nothing is loaded until turned on.
//
// 'blocked': the browser refused to start playback outside a tap (turn it on again from one).
export type ReferenceStatus = 'off' | 'on' | 'blocked' | 'error'

export interface ReferenceState {
  available: boolean // the current track has a reference file
  status: ReferenceStatus
  muted: boolean
  volume: number // 0..1, slider position; persisted
}

const STORAGE_KEY = 'backline:reference'
const DEFAULT_VOLUME = 0.3
// Correct the reference when it is this far from the main track. Two local elements
// drift by a few ms; seeks and buffering are what this catches.
const DRIFT = 0.05

interface Stored {
  enabled: boolean
  muted: boolean
  volume: number
}

type Listener = (state: ReferenceState) => void

export class Reference {
  private readonly audio: HTMLAudioElement
  private readonly main: HTMLAudioElement
  private readonly listeners = new Set<Listener>()
  private ctx: AudioContext | null = null
  private gain: GainNode | null = null
  private track: Track | null = null
  private enabled: boolean
  private state: ReferenceState

  constructor(audio: HTMLAudioElement, main: HTMLAudioElement) {
    this.audio = audio
    this.main = main
    const stored = load()
    this.enabled = stored.enabled
    this.state = { available: false, status: 'off', muted: stored.muted, volume: stored.volume }

    main.addEventListener('play', this.onMainPlay)
    main.addEventListener('pause', this.onMainPause)
    main.addEventListener('seeking', this.resync)
    main.addEventListener('timeupdate', this.onMainTime)
    main.addEventListener('ended', this.onMainPause)
    audio.addEventListener('loadedmetadata', this.resync)
    audio.addEventListener('error', () => {
      if (this.audio.getAttribute('src')) this.setStatus('error')
    })
    document.addEventListener('visibilitychange', this.resumeContext)
  }

  getState(): ReferenceState {
    return this.state
  }

  // Whether this device wants references (also decides if they are downloaded for offline use).
  isEnabled(): boolean {
    return this.enabled
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    listener(this.state)
    return () => this.listeners.delete(listener)
  }

  // Called whenever the leader loads a track (including the same one again).
  setTrack(track: Track | null): void {
    if (track?.id === this.track?.id) return
    this.track = track
    this.unload()
    this.patch({ available: Boolean(track?.reference) })
    if (this.enabled && track?.reference) this.load()
  }

  // Call from a tap: iOS only lets an element start, and an AudioContext run, inside a gesture.
  toggle(): void {
    if (this.enabled) this.turnOff()
    else this.turnOn()
  }

  setMuted(muted: boolean): void {
    this.patch({ muted })
    this.applyGain()
    this.persist()
  }

  setVolume(volume: number): void {
    this.patch({ volume: Math.min(1, Math.max(0, volume)) })
    this.applyGain()
    this.persist()
  }

  destroy(): void {
    this.main.removeEventListener('play', this.onMainPlay)
    this.main.removeEventListener('pause', this.onMainPause)
    this.main.removeEventListener('seeking', this.resync)
    this.main.removeEventListener('timeupdate', this.onMainTime)
    this.main.removeEventListener('ended', this.onMainPause)
    this.audio.removeEventListener('loadedmetadata', this.resync)
    document.removeEventListener('visibilitychange', this.resumeContext)
    this.unload()
  }

  private turnOn(): void {
    this.enabled = true
    this.persist()
    this.ensureGraph()
    void this.ctx?.resume()
    if (!this.track?.reference) {
      // Nothing to play yet; mark the element as user-started for later programmatic play().
      this.audio.play().catch(() => {})
      this.audio.pause()
      this.setStatus('on')
      return
    }
    this.load()
    this.setStatus('on')
    if (!this.main.paused) this.play()
    else this.unlock()
  }

  private turnOff(): void {
    this.enabled = false
    this.persist()
    this.audio.pause()
    this.setStatus('off')
  }

  private onMainPlay = (): void => {
    if (!this.active()) return
    this.align()
    this.play()
  }

  private onMainPause = (): void => {
    if (!this.audio.paused) this.audio.pause()
  }

  private resync = (): void => {
    if (!this.active()) return
    this.align()
  }

  private onMainTime = (): void => {
    if (!this.active() || this.main.paused || this.audio.paused) return
    if (Math.abs(this.audio.currentTime - this.main.currentTime) > DRIFT) this.align()
  }

  private active(): boolean {
    return this.enabled && this.track?.reference !== null && this.track !== null && this.audio.getAttribute('src') !== null
  }

  private align(): void {
    this.audio.currentTime = this.main.currentTime
  }

  private play(): void {
    void this.ctx?.resume()
    this.audio.play().then(
      () => this.setStatus('on'),
      (err: unknown) => {
        if (err instanceof DOMException && err.name === 'NotAllowedError') this.setStatus('blocked')
      },
    )
  }

  // play() inside the gesture marks the element as user-started, so later programmatic
  // play() calls are allowed; pausing right away keeps it silent.
  private unlock(): void {
    this.audio.play().catch(() => {})
    this.audio.pause()
  }

  private load(): void {
    const reference = this.track?.reference
    if (!reference) return
    this.audio.src = reference.url
    this.audio.load()
    this.applyGain()
  }

  private unload(): void {
    this.audio.pause()
    this.audio.removeAttribute('src')
    this.audio.load()
    if (this.enabled) this.setStatus('on')
  }

  // Built once, in a gesture: a MediaElementSource can be created only once per element.
  private ensureGraph(): void {
    if (this.ctx || typeof AudioContext === 'undefined') return
    const ctx = new AudioContext()
    const gain = ctx.createGain()
    ctx.createMediaElementSource(this.audio).connect(gain).connect(ctx.destination)
    this.ctx = ctx
    this.gain = gain
    this.applyGain()
  }

  private resumeContext = (): void => {
    if (document.visibilityState === 'visible' && this.enabled) void this.ctx?.resume()
  }

  private applyGain(): void {
    const { muted, volume } = this.state
    // Squared slider position feels linear to the ear.
    const value = muted ? 0 : volume * volume
    if (this.gain) this.gain.gain.value = value
    else this.audio.volume = value // desktop fallback before the graph exists
  }

  private setStatus(status: ReferenceStatus): void {
    if (this.state.status !== status) this.patch({ status })
  }

  private patch(partial: Partial<ReferenceState>): void {
    this.state = { ...this.state, ...partial }
    for (const listener of this.listeners) listener(this.state)
  }

  private persist(): void {
    const stored: Stored = { enabled: this.enabled, muted: this.state.muted, volume: this.state.volume }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
    } catch {
      // private mode / quota: settings just don't survive a reload
    }
  }
}

function load(): Stored {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Stored>
      return {
        enabled: Boolean(parsed.enabled),
        muted: Boolean(parsed.muted),
        volume: typeof parsed.volume === 'number' ? Math.min(1, Math.max(0, parsed.volume)) : DEFAULT_VOLUME,
      }
    }
  } catch {
    // fall through
  }
  return { enabled: false, muted: false, volume: DEFAULT_VOLUME }
}
