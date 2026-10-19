// Messages exchanged over /ws between the page and the Room Durable Object.

export type Role = 'leader' | 'follower'

export interface PlaybackState {
  trackId: string | null
  playing: boolean
  // Seconds into the track when the leader sent this.
  position: number
}

export interface Presence {
  followers: number
  leaderOnline: boolean
}

export type ClientMessage = { type: 'state'; state: PlaybackState }

export type ServerMessage =
  | ({ type: 'welcome'; role: Role; state: PlaybackState | null } & Presence)
  | { type: 'state'; state: PlaybackState }
  | ({ type: 'presence' } & Presence)

// WebSocket close codes sent by the room.
export const CLOSE_UNAUTHORIZED = 4001
export const CLOSE_DEMOTED = 4002

export const PING = 'ping'
export const PONG = 'pong'
