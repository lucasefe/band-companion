import { currentLine, type Lyrics } from './lyrics'

const GAP = '\u00b7 \u00b7 \u00b7'
// Keep the current line this far down the viewport so the next lines are visible.
const ANCHOR = 0.38
// After the user scrolls, leave the list alone for this long.
const READ_AHEAD_HOLD = 4000

export interface LyricsViewOptions {
  // When set, tapping a timed line calls it with the line's time.
  onSeek?: (time: number) => void
}

export class LyricsView {
  readonly element: HTMLElement

  private readonly list: HTMLOListElement
  private readonly empty: HTMLElement
  private readonly onSeek: ((time: number) => void) | undefined
  private readonly reducedMotion = matchMedia('(prefers-reduced-motion: reduce)')

  private lyrics: Lyrics | null = null
  private items: HTMLLIElement[] = []
  private active = -1
  private clock: (() => number) | null = null
  private frame = 0
  private holdUntil = 0
  private scrollPending = false

  constructor(options: LyricsViewOptions = {}) {
    this.onSeek = options.onSeek
    this.list = document.createElement('ol')
    this.list.className = 'lyrics-lines'
    this.empty = document.createElement('p')
    this.empty.className = 'lyrics-empty'
    this.element = document.createElement('section')
    this.element.className = 'lyrics'
    this.element.setAttribute('aria-label', 'Lyrics')
    this.element.append(this.empty, this.list)
    this.element.classList.toggle('is-seekable', this.onSeek !== undefined)

    // A tap does not fire these; only an actual scroll gesture does.
    const hold = () => {
      this.holdUntil = performance.now() + READ_AHEAD_HOLD
    }
    this.element.addEventListener('touchmove', hold, { passive: true })
    this.element.addEventListener('wheel', hold, { passive: true })

    this.setLyrics(null, 'Tap a song')
  }

  // Replaces the lyrics. `placeholder` is shown when there are none.
  setLyrics(lyrics: Lyrics | null, placeholder = 'No lyrics for this song'): void {
    this.lyrics = lyrics
    this.active = -1
    this.items = []
    this.holdUntil = 0
    this.scrollPending = false
    this.list.replaceChildren()
    this.element.scrollTop = 0

    const hasLines = lyrics !== null && lyrics.lines.length > 0
    this.empty.hidden = hasLines
    this.list.hidden = !hasLines
    this.element.classList.toggle('is-timed', hasLines && lyrics.timed)
    if (!hasLines) {
      this.empty.textContent = placeholder
      return
    }

    const seekable = this.onSeek !== undefined && lyrics.timed
    for (const line of lyrics.lines) {
      const item = document.createElement('li')
      item.className = 'lyric'
      if (line.text === '') {
        item.classList.add('is-gap')
        item.textContent = lyrics.timed ? GAP : ''
      } else {
        item.textContent = line.text
      }
      if (seekable) {
        item.setAttribute('role', 'button')
        item.tabIndex = 0
        const seek = () => {
          this.holdUntil = 0
          this.onSeek!(line.time!)
        }
        item.addEventListener('click', seek)
        item.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            seek()
          }
        })
      }
      this.items.push(item)
      this.list.append(item)
    }
  }

  // Follows `clock()` (seconds) every frame until stop().
  start(clock: () => number): void {
    this.clock = clock
    if (this.frame) return
    const tick = () => {
      if (!this.clock) return
      this.update(this.clock())
      this.frame = requestAnimationFrame(tick)
    }
    this.frame = requestAnimationFrame(tick)
  }

  stop(): void {
    this.clock = null
    if (this.frame) cancelAnimationFrame(this.frame)
    this.frame = 0
  }

  update(time: number): void {
    if (!this.lyrics?.timed) return
    const index = currentLine(this.lyrics.lines, time)
    if (index === this.active) {
      if (this.scrollPending && !this.holding()) this.scrollToActive()
      return
    }

    const previous = this.items[this.active]
    previous?.classList.remove('is-current')
    for (let i = 0; i < this.items.length; i++) {
      this.items[i]!.classList.toggle('is-past', i < index)
    }
    this.active = index
    this.items[index]?.classList.add('is-current')

    if (this.holding()) this.scrollPending = true
    else this.scrollToActive()
  }

  private holding(): boolean {
    return performance.now() < this.holdUntil
  }

  private scrollToActive(): void {
    this.scrollPending = false
    const item = this.items[this.active]
    const top = item ? item.offsetTop - this.element.clientHeight * ANCHOR + item.offsetHeight / 2 : 0
    this.element.scrollTo({ top: Math.max(0, top), behavior: this.behavior() })
  }

  private behavior(): ScrollBehavior {
    return this.reducedMotion.matches ? 'auto' : 'smooth'
  }
}
