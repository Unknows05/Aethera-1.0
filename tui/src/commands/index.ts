import type { SystemStatus, Signal } from '../types.js'

export interface CommandContext {
  status: SystemStatus
  signals: Signal[]
  baseUrl: string
  wsConnected: boolean
  wsRef: any
  addLine: (line: string) => void
  setStatus: (s: SystemStatus) => void
  setSignals: (s: Signal[]) => void
  exit: () => void
}

export type CommandHandler = (ctx: CommandContext) => Promise<void> | void

export const commands: Record<string, CommandHandler> = {}
