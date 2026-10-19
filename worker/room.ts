import { DurableObject } from 'cloudflare:workers'
import {
  CLOSE_DEMOTED,
  CLOSE_UNAUTHORIZED,
  PING,
  PONG,
  type ClientMessage,
  type PlaybackState,
  type Presence,
  type Role,
  type ServerMessage,
} from '../shared/protocol'

// One room for the whole band. The single leader pushes playback state;
// followers receive it. Uses the hibernation API so idle sockets cost nothing.
export class Room extends DurableObject<Env> {
  private state: PlaybackState | null = null

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env)
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair(PING, PONG))
    void ctx.blockConcurrencyWhile(async () => {
      this.state = (await ctx.storage.get<PlaybackState>('state')) ?? null
    })
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url)
    const role: Role = url.searchParams.get('role') === 'leader' ? 'leader' : 'follower'
    const pair = new WebSocketPair()
    const [client, server] = [pair[0], pair[1]]

    if (role === 'leader' && url.searchParams.get('key') !== this.env.LEADER_KEY) {
      server.accept()
      server.close(CLOSE_UNAUTHORIZED, 'Wrong key')
      return new Response(null, { status: 101, webSocket: client })
    }

    if (role === 'leader') {
      for (const ws of this.ctx.getWebSockets('leader')) ws.close(CLOSE_DEMOTED, 'Another leader took over')
    }

    this.ctx.acceptWebSocket(server, [role])
    this.send(server, { type: 'welcome', role, state: this.state, ...this.presence() })
    this.broadcastPresence()
    return new Response(null, { status: 101, webSocket: client })
  }

  async webSocketMessage(ws: WebSocket, raw: string | ArrayBuffer): Promise<void> {
    if (typeof raw !== 'string' || !this.ctx.getTags(ws).includes('leader')) return
    let message: ClientMessage
    try {
      message = JSON.parse(raw)
    } catch {
      return
    }
    if (message.type !== 'state') return

    this.state = message.state
    await this.ctx.storage.put('state', this.state)
    const payload = JSON.stringify({ type: 'state', state: this.state } satisfies ServerMessage)
    for (const follower of this.ctx.getWebSockets('follower')) follower.send(payload)
  }

  webSocketClose(ws: WebSocket): void {
    this.drop(ws)
  }

  webSocketError(ws: WebSocket): void {
    this.drop(ws)
  }

  private drop(ws: WebSocket): void {
    try {
      ws.close(1000, 'Closed')
    } catch {
      // Already closed.
    }
    this.broadcastPresence()
  }

  private presence(): Presence {
    return {
      followers: this.ctx.getWebSockets('follower').length,
      leaderOnline: this.ctx.getWebSockets('leader').length > 0,
    }
  }

  private broadcastPresence(): void {
    const payload = JSON.stringify({ type: 'presence', ...this.presence() } satisfies ServerMessage)
    for (const ws of this.ctx.getWebSockets()) {
      try {
        ws.send(payload)
      } catch {
        // Closing sockets throw; they drop out of getWebSockets() on their own.
      }
    }
  }

  private send(ws: WebSocket, message: ServerMessage): void {
    ws.send(JSON.stringify(message))
  }
}
