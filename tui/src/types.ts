export interface Signal {
  symbol: string
  signal: 'LONG' | 'SHORT' | 'WAIT'
  confidence: number
  composite_score: number
  regime: string
  timestamp: string
  reasons?: string[]
  debate_signal?: 'LONG' | 'SHORT' | 'WAIT'
  debate_confidence?: number
  debate_overrode?: boolean
}

export interface DebateStats {
  total: number
  longs: number
  shorts: number
  waits: number
  avg_confidence: number
  overrides: number
}

export interface SystemStatus {
  ok: boolean
  mode: string
  balance: number | null
  scan_count: number
  last_scan: string
  is_scanning: boolean
  strategy: {
    pairs: string[]
    direction: string
    confidence_threshold: number
    max_trades: number
  }
  debate_stats: DebateStats
  error?: string
}

export interface SignalsResponse {
  ok: boolean
  data: Signal[]
  summary?: {
    total: number
    long: number
    short: number
  }
}
