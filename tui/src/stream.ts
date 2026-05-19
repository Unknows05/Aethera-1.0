import ky from 'ky'
import { createRequire } from 'module'
import type { WsMessage } from './api.js'

const require = createRequire(import.meta.url)
const _WebSocket: any = require('ws')

export { type WsMessage } from './api.js'

export function getWsUrl(baseUrl: string): string {
  const url = new URL(baseUrl)
  const proto = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${url.host}/ws`
}

export function connectWs(
  baseUrl: string,
  onMessage: (msg: WsMessage) => void,
  onOpen: () => void,
  onClose: () => void,
): any {
  const ws = new _WebSocket(getWsUrl(baseUrl))
  ws.onopen = () => onOpen()
  ws.onmessage = (e: any) => {
    try {
      const msg = JSON.parse(e.data) as WsMessage
      onMessage(msg)
    } catch { /* ignore parse errors */ }
  }
  ws.onclose = () => onClose()
  ws.onerror = () => { /* suppress crash on connection failure */ }
  return ws
}

export function wsSendScan(ws: any): void {
  ws.send(JSON.stringify({ type: 'scan' }))
}

export function wsPing(ws: any): void {
  ws.send(JSON.stringify({ type: 'ping' }))
}

export interface StreamManager {
  ws: any | null
  connected: boolean
  connect: (baseUrl: string, handlers: StreamHandlers) => any
  disconnect: () => void
}

export interface StreamHandlers {
  onInit?: (data: Record<string, unknown>) => void
  onScanComplete?: (data: Record<string, unknown>) => void
  onSignal?: (data: Record<string, unknown>) => void
  onStatus?: (data: Record<string, unknown>) => void
  onConnect?: () => void
  onDisconnect?: () => void
}

export function createStream(): StreamManager {
  return {
    ws: null,
    connected: false,

    connect(baseUrl: string, handlers: StreamHandlers) {
      this.ws = connectWs(
        baseUrl,
        (msg: WsMessage) => {
          switch (msg.type) {
            case 'init':
              handlers.onInit?.(msg.data)
              break
            case 'scan_complete':
              handlers.onScanComplete?.(msg.data)
              break
            case 'signal':
              handlers.onSignal?.(msg.data)
              break
            case 'status':
              handlers.onStatus?.(msg.data)
              break
          }
        },
        () => {
          this.connected = true
          handlers.onConnect?.()
        },
        () => {
          this.connected = false
          handlers.onDisconnect?.()
        },
      )
      return this.ws
    },

    disconnect() {
      if (this.ws) {
        this.ws.close()
        this.ws = null
        this.connected = false
      }
    },
  }
}
