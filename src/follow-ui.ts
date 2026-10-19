import { el } from './dom'
import { LyricsView } from './lyrics-view'
import type { MonitorStatus } from './monitor'
import type { FollowerView } from './sync'
import { findTrack, type TrackSet } from './tracks'
import type { WakeLockStatus } from './wakelock'

export interface FollowHandlers {
  onLead: (key: string) => void
  onListen: () => void
  onReload: () => void
  onTap: () => void
}

// Read-only view of whatever the leader is playing. No audio, no transport.
export class FollowUI {
  private readonly handlers: FollowHandlers
  private readonly sets: TrackSet[]
  private readonly lyrics: LyricsView

  private readonly liveChip: HTMLSpanElement
  private readonly wakeChip: HTMLSpanElement
  private readonly listenChip: HTMLButtonElement
  private readonly nowTitle: HTMLElement
  private readonly nowSet: HTMLElement
  private readonly dialog: HTMLDialogElement
  private readonly keyInput: HTMLInputElement
  private readonly toast: HTMLElement
  private readonly notice: HTMLElement

  private lastTrackId: string | null = null
  private clock: (() => number) | null = null

  constructor(root: HTMLElement, sets: TrackSet[], handlers: FollowHandlers) {
    this.sets = sets
    this.handlers = handlers

    this.liveChip = el('span', { class: 'chip chip-live', role: 'status' })
    this.wakeChip = el('span', { class: 'chip chip-wake', role: 'status' })
    this.listenChip = el('button', { class: 'chip chip-listen', type: 'button', 'aria-pressed': 'false' })
    this.listenChip.addEventListener('click', () => this.handlers.onListen())
    const leadButton = el('button', { class: 'chip chip-lead', type: 'button', text: 'Lead' })
    leadButton.addEventListener('click', () => this.openDialog())

    this.nowTitle = el('div', { class: 'now-title', text: 'Waiting for the leader\u2026' })
    this.nowSet = el('div', { class: 'now-set' })
    this.lyrics = new LyricsView()
    this.lyrics.setLyrics(null, '')

    this.keyInput = el('input', {
      class: 'field',
      type: 'password',
      name: 'key',
      autocomplete: 'current-password',
      autocapitalize: 'none',
      placeholder: 'Leader key',
      required: true,
    })
    const cancel = el('button', { class: 'transport', type: 'button', text: 'Cancel' })
    cancel.addEventListener('click', () => this.dialog.close())
    const form = el('form', { class: 'lead-form', method: 'dialog' }, [
      el('h2', { class: 'lead-title', text: 'Lead the band' }),
      el('p', { class: 'lead-hint', text: 'This device will play the music and everyone else will follow its lyrics.' }),
      this.keyInput,
      el('div', { class: 'transport-row lead-actions' }, [
        cancel,
        el('button', { class: 'transport transport-play', type: 'submit', text: 'Lead' }),
      ]),
    ])
    form.addEventListener('submit', (event) => {
      event.preventDefault()
      const key = this.keyInput.value.trim()
      if (!key) return
      this.dialog.close()
      this.handlers.onLead(key)
    })
    this.dialog = el('dialog', { class: 'lead-dialog' }, [form])

    const reload = el('button', { class: 'toast-action', type: 'button', text: 'Reload' })
    reload.addEventListener('click', () => this.handlers.onReload())
    this.toast = el('div', { class: 'toast', hidden: true }, [el('span', { text: 'Update available' }), reload])
    this.notice = el('div', { class: 'toast toast-notice', role: 'status', hidden: true })

    root.replaceChildren(
      el('header', { class: 'topbar' }, [
        el('h1', { class: 'brand', text: 'Band Companion' }),
        el('div', { class: 'chips' }, [this.liveChip, this.wakeChip, this.listenChip, leadButton]),
      ]),
      el('div', { class: 'follow-now' }, [this.nowTitle, this.nowSet]),
      this.lyrics.element,
      this.dialog,
      this.toast,
      this.notice,
    )
    root.classList.add('follow')
    root.classList.remove('view-songs', 'view-lyrics')
    // pointerup, not pointerdown: on touch only the former counts as a user
    // gesture, which the wake lock request needs.
    root.addEventListener('pointerup', () => this.handlers.onTap())
  }

  update(view: FollowerView, clock: () => number): void {
    this.clock = clock
    this.updateChip(view)

    const state = view.state
    const track = state?.trackId ? findTrack(this.sets, state.trackId) : null

    this.nowTitle.textContent = !state
      ? 'Waiting for the leader\u2026'
      : track
        ? track.title
        : state.trackId
          ? 'Unknown song'
          : 'Nothing loaded'
    this.nowSet.textContent = track && this.sets.length > 1 ? track.setName : ''

    const trackId = state?.trackId ?? null
    if (trackId !== this.lastTrackId) {
      this.lastTrackId = trackId
      // An unknown id means this device runs an older build than the leader.
      this.lyrics.setLyrics(track?.lyrics ?? null, trackId && !track ? 'Reload to get the latest songs' : '')
    }

    if (state?.playing) this.lyrics.start(this.clock)
    else {
      this.lyrics.stop()
      this.lyrics.update(state?.position ?? 0)
    }
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
        chip.textContent = 'Tap to stay awake'
        break
      case 'denied':
      case 'unsupported':
        chip.textContent = 'Can\u2019t keep awake'
        chip.classList.add('is-warn')
        break
    }
  }

  updateListen(status: MonitorStatus): void {
    const chip = this.listenChip
    chip.classList.remove('is-ok', 'is-warn')
    chip.setAttribute('aria-pressed', String(status === 'on'))
    switch (status) {
      case 'off':
        chip.textContent = 'Muted'
        break
      case 'on':
        chip.textContent = 'Listening'
        chip.classList.add('is-ok')
        break
      case 'blocked':
        chip.textContent = 'Tap to listen'
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

  destroy(): void {
    this.lyrics.stop()
  }

  private openDialog(): void {
    this.keyInput.value = ''
    this.dialog.showModal()
    this.keyInput.focus()
  }

  private updateChip(view: FollowerView): void {
    const chip = this.liveChip
    chip.classList.remove('is-ok', 'is-warn', 'is-busy')
    switch (view.status) {
      case 'live':
        if (view.leaderOnline) {
          chip.textContent = 'Live'
          chip.classList.add('is-ok')
        } else {
          chip.textContent = 'Leader offline'
          chip.classList.add('is-warn')
        }
        break
      case 'connecting':
        chip.textContent = 'Connecting\u2026'
        chip.classList.add('is-busy')
        break
      case 'offline':
        chip.textContent = 'Offline'
        chip.classList.add('is-warn')
        break
    }
  }
}
