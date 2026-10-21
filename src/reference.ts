import type { Track } from './tracks'

// The original song, mixed under the drums on the leader device. It is a second <audio>
// element locked to the main one and routed through a Web Audio gain node, because iOS
// ignores HTMLMediaElement.volume. Off by default; nothing is loaded until turned on.
//
// 'blocked': the browser refused to start playback outside a tap (turn it on again from one).
export type ReferenceStatus = 'off' | 'loading' | 'on' | 'blocked' | 'error'

// Turns a track's reference URL into something the element can play; on the leader this
// downloads the file into the offline cache first and returns a blob: URL.
export type SourceResolver = (url: string) => Promise<string>

export interface ReferenceState {
  available: boolean // the current track has a reference file
  status: ReferenceStatus
  muted: boolean
  volume: number // 0..1, slider position; persisted
}

const STORAGE_KEY = 'backline:reference'
const DEFAULT_VOLUME = 0.3
// Keeping two elements together without stutter. Both corrections are audible if overused:
// a seek interrupts the audio and makes the element re-buffer; a playbackRate change engages
// the browser's time-stretcher, which crackles on every change (Firefox and WebKit more than
// Chrome). So: seeks only for large gaps and the leader's own seeks; a fixed, gentle rate nudge
// with hysteresis on a smoothed drift reading, which in steady state never engages at all.
const JUMP_DRIFT = 0.3 // seconds; beyond this, jump
const JUMP_COOLDOWN = 2000 // ms; let a jump settle before judging again
// Right after a start or a seek, whichever element re-buffers slower ends up behind by 100–200 ms.
// One follow-up jump fixes that while the interruption is still fresh; after that, only nudges.
const CORRECTION_DRIFT = 0.08
const NUDGE_ON = 0.06 // seconds of smoothed drift to start nudging
const NUDGE_OFF = 0.01 // ... and to stop
const NUDGE_RATE = 0.015 // ±1.5 % playback rate, fixed
const SMOOTHING = 0.35 // EMA factor on the drift reading; two currentTimes jitter by ~10 ms

interface Stored {
  enabled: boolean
  muted: boolean
  volume: number
}

type Listener = (state: ReferenceState) => void

export class Reference {
  private readonly audio: HTMLAudioElement
  private readonly main: HTMLAudioElement
  private readonly resolve: SourceResolver
  private readonly listeners = new Set<Listener>()
  private loadToken = 0
  private ctx: AudioContext | null = null
  private gain: GainNode | null = null
  private track: Track | null = null
  private enabled: boolean
  private state: ReferenceState
  private lastJumpAt = 0
  private corrected = false // the one follow-up jump after the last deliberate jump was used
  private drift: number | null = null // smoothed main − reference, seconds (> 0: reference late)
  private nudging = 0 // -1, 0, +1

  constructor(audio: HTMLAudioElement, main: HTMLAudioElement, resolve: SourceResolver = async (u) => u) {
    this.audio = audio
    this.main = main
    this.resolve = resolve
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
    // Inside the tap: mark the element as user-started so later programmatic play() calls
    // (after the download) are allowed.
    this.unlock()
    if (!this.track?.reference) {
      this.setStatus('on')
      return
    }
    this.load()
  }

  private turnOff(): void {
    this.enabled = false
    this.persist()
    this.audio.pause()
    this.setStatus('off')
  }

  private onMainPlay = (): void => {
    if (!this.active()) return
    this.jump()
    this.play()
  }

  private onMainPause = (): void => {
    if (!this.audio.paused) this.audio.pause()
    this.stopNudging()
    this.drift = null
  }

  // The leader seeked on purpose: follow immediately, a glitch is expected here.
  private resync = (): void => {
    if (!this.active()) return
    this.jump()
  }

  private onMainTime = (): void => {
    if (!this.active() || this.main.paused || this.audio.paused || this.audio.seeking) return
    const raw = this.main.currentTime - this.audio.currentTime // > 0: reference is late
    if (performance.now() - this.lastJumpAt < JUMP_COOLDOWN) return // let the jump settle

    this.drift = this.drift === null ? raw : this.drift + SMOOTHING * (raw - this.drift)
    const drift = this.drift

    if (Math.abs(raw) > JUMP_DRIFT) {
      this.jump()
      return
    }
    if (!this.corrected && Math.abs(drift) > CORRECTION_DRIFT) {
      this.jump()
      this.corrected = true
      return
    }
    if (this.nudging === 0) {
      if (Math.abs(drift) > NUDGE_ON) {
        this.nudging = Math.sign(drift)
        this.audio.playbackRate = 1 + this.nudging * NUDGE_RATE
      }
    } else if (Math.abs(drift) < NUDGE_OFF || Math.sign(drift) !== this.nudging) {
      this.stopNudging()
    }
  }

  private stopNudging(): void {
    this.nudging = 0
    if (this.audio.playbackRate !== 1) this.audio.playbackRate = 1
  }

  private active(): boolean {
    return this.enabled && this.track?.reference !== null && this.track !== null && this.audio.getAttribute('src') !== null
  }

  private jump(): void {
    this.stopNudging()
    this.drift = null
    this.corrected = false
    this.audio.currentTime = this.main.currentTime
    this.lastJumpAt = performance.now()
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
    const token = ++this.loadToken
    this.setStatus('loading')
    this.resolve(reference.url).then(
      (src) => {
        if (token !== this.loadToken || !this.enabled) return
        this.audio.src = src
        this.audio.load()
        this.applyGain()
        this.setStatus('on')
        if (!this.main.paused) {
          this.jump()
          this.play()
        }
      },
      () => {
        if (token === this.loadToken) this.setStatus('error')
      },
    )
  }

  private unload(): void {
    this.loadToken += 1
    this.stopNudging()
    this.drift = null
    this.audio.pause()
    const old = this.audio.getAttribute('src')
    this.audio.removeAttribute('src')
    this.audio.load()
    if (old?.startsWith('blob:')) URL.revokeObjectURL(old)
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
