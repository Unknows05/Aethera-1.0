import ky from 'ky'
import { createRequire } from 'module'
import type { SystemStatus, SignalsResponse } from './types.js'

const require = createRequire(import.meta.url)
const _WebSocket: any = require('ws')

const DEFAULT_BASE = 'http://127.0.0.1:8000'

const api = (base: string) => {
  const apiKey = process.env.AETHERA_API_KEY || ''
  const headers: Record<string, string> = {}
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }
  return ky.create({
    prefixUrl: base,
    timeout: 10_000,
    retry: { limit: 0 },
    headers,
  })
}

export async function fetchStatus(baseUrl: string): Promise<SystemStatus> {
  try {
    return await api(baseUrl).get('api/tui/status').json<SystemStatus>()
  } catch {
    return {
      ok: false,
      mode: 'OFFLINE',
      balance: null,
      scan_count: 0,
      last_scan: '-',
      is_scanning: false,
      strategy: { pairs: [], direction: 'BOTH', confidence_threshold: 0, max_trades: 3 },
      debate_stats: { total: 0, longs: 0, shorts: 0, waits: 0, avg_confidence: 0, overrides: 0 },
      error: 'Cannot connect to backend',
    }
  }
}

export async function fetchSignals(baseUrl: string): Promise<SignalsResponse> {
  try {
    return await api(baseUrl).get('api/signals').json<SignalsResponse>()
  } catch {
    return { ok: false, data: [] }
  }
}

export async function triggerScan(baseUrl: string): Promise<{ ok: boolean }> {
  try {
    return await api(baseUrl).post('api/scan').json<{ ok: boolean }>()
  } catch {
    return { ok: false }
  }
}

export function getWsUrl(baseUrl: string): string {
  const url = new URL(baseUrl)
  const proto = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${url.host}/ws`
}

export interface WsMessage {
  type: 'init' | 'scan_complete' | 'pong' | 'signal' | 'status' | 'trade' | 'alert' | 'debate'
  data: Record<string, unknown>
  timestamp?: string
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

