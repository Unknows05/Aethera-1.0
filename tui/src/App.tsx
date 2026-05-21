import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Box, Text, useApp, useInput } from 'ink'
import Header from './components/header.js'
import Transcript from './components/transcript.js'
import CommandInput from './components/input.js'
import Modal from './components/modal.js'
import { fetchStatus, fetchSignals, triggerScan } from './api.js'
import type { WsMessage } from './api.js'
import { connectWs, wsSendScan, wsPing } from './stream.js'
import { commands } from './commands/registry.js'
import type { Signal, SystemStatus } from './types.js'

interface Props {
  baseUrl: string
}

function signalColor(sig: string): string {
  if (sig === 'LONG') return 'green'
  if (sig === 'SHORT') return 'red'
  return 'gray'
}

export default function App({ baseUrl }: Props) {
  const { exit } = useApp()

  const [status, setStatus] = useState<SystemStatus>({
    ok: false, mode: 'OFFLINE', balance: null, scan_count: 0,
    last_scan: '-', is_scanning: false,
    strategy: { pairs: [], direction: 'BOTH', confidence_threshold: 0, max_trades: 3 },
    debate_stats: { total: 0, longs: 0, shorts: 0, waits: 0, avg_confidence: 0, overrides: 0 },
  })
  const [signals, setSignals] = useState<Signal[]>([])
  const [terminalLines, setTerminalLines] = useState<string[]>([
    '  Aethera v1.6 TUI — Type /help for commands',
    '',
  ])
  const [showInput, setShowInput] = useState(true)
  const [wsConnected, setWsConnected] = useState(false)
  const [inputKey, setInputKey] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)
  const pingTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  const addLine = useCallback((line: string) => {
    setTerminalLines(prev => [...prev.slice(-50), line])
  }, [])

  const commandContext = {
    status, signals, baseUrl, wsConnected, wsRef,
    addLine, setStatus, setSignals, exit,
  }

  // WebSocket connection
  useEffect(() => {
    const ws = connectWs(
      baseUrl,
      (msg: WsMessage) => {
        switch (msg.type) {
          case 'init': {
            const d = msg.data as { status?: SystemStatus; signals?: Signal[] }
            if (d.status) setStatus(d.status)
            if (d.signals) setSignals(d.signals)
            break
          }
          case 'scan_complete': {
            addLine('  [green]Scan complete[/]')
            fetchSignals(baseUrl).then(r => {
              if (r.ok) setSignals(r.data)
            })
            fetchStatus(baseUrl).then(s => setStatus(s))
            break
          }
          case 'signal': {
            const d = msg.data as { symbol?: string; signal?: string; confidence?: number }
            if (d.symbol && d.signal) {
              addLine(`  [green]Signal[/] ${d.symbol} → ${d.signal} (${d.confidence || 0}%)`)
              fetchSignals(baseUrl).then(r => { if (r.ok) setSignals(r.data) })
            }
            break
          }
          case 'status': {
            const d = msg.data as unknown as SystemStatus | undefined
            if (d) {
              setStatus(d)
              addLine(`  [dim]Status update — ${d.scan_count} scans, ${d.mode}[/]`)
            }
            break
          }
          case 'trade': {
            const d = msg.data as { symbol?: string; action?: string; pnl_pct?: number }
            if (d.symbol) {
              addLine(`  [yellow]Trade[/] ${d.symbol} ${d.action || 'execute'} ${d.pnl_pct != null ? `(${d.pnl_pct > 0 ? '+' : ''}${d.pnl_pct.toFixed(1)}%)` : ''}`)
            }
            break
          }
          case 'alert': {
            const d = msg.data as { message?: string; level?: string }
            const color = d.level === 'critical' ? 'red' : d.level === 'warning' ? 'yellow' : 'dim'
            addLine(`  [${color}]ALERT[/] ${d.message || 'Unknown alert'}`)
            break
          }
          case 'debate': {
            const d = msg.data as { symbol?: string; signal?: string; confidence?: number }
            if (d.symbol) {
              addLine(`  [cyan]Debate[/] ${d.symbol} → ${d.signal || 'WAIT'} (${d.confidence || 0}%)`)
            }
            break
          }
          case 'pong':
            break
        }
      },
      () => {
        setWsConnected(true)
        addLine('  [green]WebSocket connected[/]')
        pingTimer.current = setInterval(() => {
          if (wsRef.current) wsPing(wsRef.current)
        }, 30_000)
      },
      () => {
        setWsConnected(false)
        if (pingTimer.current) clearInterval(pingTimer.current)
      },
    )
    wsRef.current = ws

    return () => {
      ws.close()
      if (pingTimer.current) clearInterval(pingTimer.current)
    }
  }, [baseUrl, addLine])

  const handleCommand = useCallback(async (cmd: string) => {
    const trimmed = cmd.trim()
    if (!trimmed) return

    addLine(`> ${trimmed}`)

    const handler = commands[trimmed]
    if (handler) {
      await handler(commandContext)
    } else if (trimmed === '/scan') {
      addLine('  Triggering scan via WebSocket...')
      if (wsRef.current && wsConnected) {
        wsSendScan(wsRef.current)
      } else {
        const res = await triggerScan(baseUrl)
        addLine(res.ok ? '  Scan triggered (HTTP fallback)' : '  Scan failed')
        if (res.ok) {
          fetchSignals(baseUrl).then(r => { if (r.ok) setSignals(r.data) })
          fetchStatus(baseUrl).then(s => setStatus(s))
        }
      }
      addLine('')
    } else {
      addLine(`  Unknown command: ${trimmed}`)
      addLine('')
    }
  }, [baseUrl, status, signals, addLine, exit, wsConnected])

  useInput((input, key) => {
    if (input === 'q' || input === 'Q') {
      exit()
    }
  })

  const onInputSubmit = (value: string) => {
    handleCommand(value)
    setInputKey(k => k + 1)
  }

  const width = process.stdout.columns || 120
  const ds = status.debate_stats

  // Signals table
  const sigHeader = `  Symbol       Signal  Conf  Score  Regime     Reason`
  const sigDivider = `  ${'─'.repeat(12)} ${'─'.repeat(6)} ${'─'.repeat(4)} ${'─'.repeat(5)} ${'─'.repeat(8)} ${'─'.repeat(25)}`

  const sigRows = signals.slice(0, 12).map(s => {
    const debate = s.debate_overrode ? ` ⚡${s.debate_signal}` : ''
    const reason = (s.reasons?.[0] || '').slice(0, 25)
    return `  ${s.symbol.padEnd(12)} ${s.signal.padEnd(5)} ${String(s.confidence).padStart(4)}  ${s.composite_score.toFixed(1).padStart(5)}  ${s.regime.padEnd(8)} ${reason}${debate}`
  })

  const longs = signals.filter(s => s.signal === 'LONG').length
  const shorts = signals.filter(s => s.signal === 'SHORT').length
  const waits = signals.filter(s => s.signal === 'WAIT').length
  const summary = `  ${longs}L / ${shorts}S / ${waits}W  |  Total: ${signals.length}`

  const debateLines = ds.total > 0
    ? [
        `  Debates: ${ds.total}`,
        `  LONG:  ${ds.longs}`,
        `  SHORT: ${ds.shorts}`,
        `  WAIT:  ${ds.waits}`,
        `  Avg:   ${ds.avg_confidence.toFixed(0)}%`,
        `  Override: ${ds.overrides}`,
      ]
    : ['  No debates yet']

  const termLines = terminalLines.slice(-8)

  return (
    <Box flexDirection="column" width={width}>
      <Header status={status} wsConnected={wsConnected} />

      {/* Signals panel */}
      <Box marginTop={1}>
        <Box flexDirection="column" width={Math.floor(width * 0.65)}>
          <Text bold color="cyan">Signals</Text>
          <Text>{sigHeader}</Text>
          <Text color="gray">{sigDivider}</Text>
          {sigRows.length === 0 && <Text color="gray">  No signals yet...</Text>}
          {sigRows.map((row, i) => {
            const sig = signals[i]
            const color = sig ? signalColor(sig.signal) : 'gray'
            return <Text key={i} color={color}>{row}</Text>
          })}
          <Text color="gray">{summary}</Text>
        </Box>

        {/* Sidebar */}
        <Box flexDirection="column" width={Math.floor(width * 0.35)} paddingLeft={2}>
          <Text bold color="cyan">Status</Text>
          <Text>  Pairs: {status.strategy.pairs.length}</Text>
          <Text>  Dir: <Text color={signalColor(status.strategy.direction)}>{status.strategy.direction}</Text></Text>
          <Text>  Conf≥: {status.strategy.confidence_threshold}</Text>
          <Text>  Max: {status.strategy.max_trades}</Text>

          <Box marginTop={1}>
            <Text bold color="cyan">Debate</Text>
          </Box>
          {debateLines.map((l, i) => (
            <Text key={i} color="gray">{l}</Text>
          ))}
        </Box>
      </Box>

      {/* Transcript */}
      <Transcript lines={termLines} width={width} />

      {/* Input */}
      {showInput && <CommandInput onSubmit={onInputSubmit} inputKey={inputKey} />}

      {/* Footer */}
      <Box marginTop={1}>
        <Text color="gray">q=quit  /status  /signals  /scan  /debate  /strategy  /help</Text>
      </Box>
    </Box>
  )
}
