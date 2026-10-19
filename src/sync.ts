import {
  CLOSE_DEMOTED,
  CLOSE_UNAUTHORIZED,
  PING,
  PONG,
  type ClientMessage,
  type PlaybackState,
  type Role,
  type ServerMessage,
} from '../shared/protocol'

export type ConnectionStatus = 'connecting' | 'live' | 'offline'
// Terminal: the room refused this connection and it will not retry.
export type ConnectionEnd = 'unauthorized' | 'demoted'

interface ConnectionHandlers {
  onStatus: (status: ConnectionStatus) => void
  onMessage: (message: ServerMessage) => void
  onEnd: (reason: ConnectionEnd) => void
}

const PING_INTERVAL = 25_000
const MAX_BACKOFF = 15_000
const HEARTBEAT = 2_000
// A position this far from where the clock expected it counts as a seek.
const SEEK_THRESHOLD = 1

function socketUrl(role: Role, key?: string): string {
  const url = new URL('/ws', location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('role', role)
  if (key) url.searchParams.set('key', key)
  return url.href
}

// Reconnects with backoff, and immediately when the page comes back (iOS drops
// sockets in the background) or the network returns.
class Connection {
  private ws: WebSocket | null = null
  private attempts = 0
  private timer = 0
  private pinger = 0
  private closed = false

  constructor(
    private readonly url: string,
    private readonly handlers: ConnectionHandlers,
  ) {
    document.addEventListener('visibilitychange', this.resume)
    window.addEventListener('online', this.resume)
    this.open()
  }

  send(message: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(message))
    return true
  }

  close(): void {
    this.closed = true
    document.removeEventListener('visibilitychange', this.resume)
    window.removeEventListener('online', this.resume)
    clearTimeout(this.timer)
    clearInterval(this.pinger)
    const ws = this.ws
    this.ws = null
    ws?.close(1000)
  }

  private open(): void {
    if (this.closed) return
    this.handlers.onStatus('connecting')
    const ws = new WebSocket(this.url)
    this.ws = ws

    ws.onopen = () => {
      this.attempts = 0
      this.pinger = window.setInterval(() => ws.send(PING), PING_INTERVAL)
      this.handlers.onStatus('live')
    }
    ws.onmessage = (event) => {
      if (event.data === PONG) return
      let message: ServerMessage
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      this.handlers.onMessage(message)
    }
    ws.onclose = (event) => {
      clearInterval(this.pinger)
      if (this.ws !== ws) return
      this.ws = null
      if (event.code === CLOSE_UNAUTHORIZED || event.code === CLOSE_DEMOTED) {
        this.close()
        this.handlers.onEnd(event.code === CLOSE_UNAUTHORIZED ? 'unauthorized' : 'demoted')
        return
      }
      this.handlers.onStatus('offline')
      const delay = Math.min(MAX_BACKOFF, 1000 * 2 ** this.attempts)
      this.attempts += 1
      this.timer = window.setTimeout(() => this.open(), delay)
    }
  }

  private resume = (): void => {
    if (this.closed || this.ws || document.visibilityState !== 'visible') return
    clearTimeout(this.timer)
    this.attempts = 0
    this.open()
  }
}

export interface LeaderLink {
  status: ConnectionStatus
  followers: number
}

interface BroadcasterHandlers {
  onLink: (link: LeaderLink) => void
  onEnd: (reason: ConnectionEnd) => void
}

// Leader side. Sends on every transition or seek and every HEARTBEAT while playing.
export class Broadcaster {
  private readonly connection: Connection
  private readonly heartbeat: number
  private link: LeaderLink = { status: 'connecting', followers: 0 }
  private last: PlaybackState | null = null
  private sentAt = 0

  constructor(
    key: string,
    private readonly getState: () => PlaybackState,
    private readonly handlers: BroadcasterHandlers,
  ) {
    this.connection = new Connection(socketUrl('leader', key), {
      onStatus: (status) => this.setLink({ status }),
      onMessage: (message) => {
        if (message.type === 'welcome') {
          this.setLink({ followers: message.followers })
          this.send(this.getState())
        } else if (message.type === 'presence') {
          this.setLink({ followers: message.followers })
        }
      },
      onEnd: handlers.onEnd,
    })
    this.heartbeat = window.setInterval(() => {
      if (this.last?.playing) this.send(this.getState())
    }, HEARTBEAT)
  }

  // Call whenever the player state changes.
  sync(): void {
    const state = this.getState()
    const last = this.last
    if (last) {
      const expected = last.playing ? last.position + (performance.now() - this.sentAt) / 1000 : last.position
      const unchanged =
        last.trackId === state.trackId &&
        last.playing === state.playing &&
        Math.abs(state.position - expected) < SEEK_THRESHOLD
      if (unchanged) return
    }
    this.send(state)
  }

  close(): void {
    clearInterval(this.heartbeat)
    this.connection.close()
  }

  private send(state: PlaybackState): void {
    this.last = state
    this.sentAt = performance.now()
    this.connection.send({ type: 'state', state })
  }

  private setLink(partial: Partial<LeaderLink>): void {
    this.link = { ...this.link, ...partial }
    this.handlers.onLink(this.link)
  }
}

export interface FollowerView {
  status: ConnectionStatus
  leaderOnline: boolean
  state: PlaybackState | null
}

// Follower side. The position runs on the local clock between updates.
export class Follower {
  private readonly connection: Connection
  private view: FollowerView = { status: 'connecting', leaderOnline: false, state: null }
  private receivedAt = 0

  constructor(private readonly onChange: (view: FollowerView) => void) {
    this.connection = new Connection(socketUrl('follower'), {
      onStatus: (status) => this.setView({ status }),
      onMessage: (message) => {
        if (message.type === 'welcome') {
          this.receivedAt = performance.now()
          this.setView({ leaderOnline: message.leaderOnline, state: message.state })
        } else if (message.type === 'state') {
          this.receivedAt = performance.now()
          this.setView({ state: message.state })
        } else if (message.type === 'presence') {
          this.setView({ leaderOnline: message.leaderOnline })
        }
      },
      onEnd: () => this.setView({ status: 'offline' }),
    })
  }

  // Current position in seconds, extrapolated while playing.
  now(): number {
    const state = this.view.state
    if (!state) return 0
    if (!state.playing) return state.position
    return state.position + (performance.now() - this.receivedAt) / 1000
  }

  close(): void {
    this.connection.close()
  }

  private setView(partial: Partial<FollowerView>): void {
    this.view = { ...this.view, ...partial }
    this.onChange(this.view)
  }
}
