import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Box, Text, useApp, useInput } from 'ink'
import { TextInput } from '@inkjs/ui'
import { fetchStatus, fetchSignals, triggerScan, connectWs, wsSendScan, wsPing, type WsMessage } from './api.js'
import type { Signal, SystemStatus, SignalsResponse } from './types.js'

interface Props {
  baseUrl: string
}

const COMMANDS = ['/status', '/signals', '/scan', '/debate', '/strategy', '/balance', '/help', '/stop']

function modeLabel(mode: string): string {
  if (mode.includes('TRADE')) return 'LIVE[TRADE]'
  if (mode.includes('SIGNALS')) return 'LIVE[SIGNALS]'
  if (mode === 'DRY-RUN') return 'DRY-RUN'
  return 'OFFLINE'
}

function modeColor(mode: string): string {
  if (mode.includes('TRADE') || mode.includes('SIGNALS')) return 'green'
  if (mode === 'DRY-RUN') return 'yellow'
  return 'red'
}

function signalColor(sig: string): string {
  if (sig === 'LONG') return 'green'
  if (sig === 'SHORT') return 'red'
  return 'gray'
}

function regimeColor(regime: string): string {
  if (regime === 'BULL') return 'green'
  if (regime === 'BEAR') return 'red'
  if (regime === 'HIGH_VOL') return 'yellow'
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
    '  Aethera v1.5 TUI — Type /help for commands',
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
            // Fetch fresh signals after scan
            fetchSignals(baseUrl).then(r => {
              if (r.ok) setSignals(r.data)
            })
            fetchStatus(baseUrl).then(s => setStatus(s))
            break
          }
          case 'pong':
            break
        }
      },
      () => {
        setWsConnected(true)
        addLine('  [green]WebSocket connected[/]')
        // Keep-alive ping every 30s
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

    switch (trimmed) {
      case '/help':
        addLine('  Commands:')
        addLine('    /status    — Show system status')
        addLine('    /signals   — Show latest signals')
        addLine('    /scan      — Trigger manual scan')
        addLine('    /debate    — Show debate stats')
        addLine('    /strategy  — Show LLM strategy')
        addLine('    /balance   — Show balance')
        addLine('    /stop      — Exit TUI')
        addLine('')
        break

      case '/status':
        addLine(`  Mode: ${modeLabel(status.mode)} | Balance: ${status.balance ? '$' + status.balance.toFixed(2) : 'N/A'}`)
        addLine(`  Scans: ${status.scan_count} | Last: ${status.last_scan}`)
        addLine(`  Strategy: ${status.strategy.pairs.length} pairs | Dir: ${status.strategy.direction} | Conf≥${status.strategy.confidence_threshold}`)
        addLine('')
        break

      case '/signals':
        addLine(`  ${signals.length} signals in last scan`)
        for (const s of signals.slice(0, 8)) {
          const debate = s.debate_overrode ? ` [⚡${s.debate_signal}]` : ''
          addLine(`    ${s.symbol.padEnd(12)} ${s.signal.padEnd(5)} conf=${String(s.confidence).padStart(2)} score=${s.composite_score.toFixed(1).padStart(5)} ${s.regime}${debate}`)
        }
        addLine('')
        break

      case '/scan':
        addLine('  Triggering scan via WebSocket...')
        if (wsRef.current && wsConnected) {
          wsSendScan(wsRef.current)
        } else {
          // Fallback to HTTP
          const res = await triggerScan(baseUrl)
          addLine(res.ok ? '  Scan triggered (HTTP fallback)' : '  Scan failed')
          if (res.ok) {
            fetchSignals(baseUrl).then(r => { if (r.ok) setSignals(r.data) })
            fetchStatus(baseUrl).then(s => setStatus(s))
          }
        }
        addLine('')
        break

      case '/debate': {
        const d = status.debate_stats
        if (d.total > 0) {
          addLine(`  Debates: ${d.total} | L:${d.longs} S:${d.shorts} W:${d.waits} | Avg conf: ${d.avg_confidence.toFixed(0)}% | Overrides: ${d.overrides}`)
        } else {
          addLine('  No debates yet (signals need conf >= 50)')
        }
        addLine('')
        break
      }

      case '/strategy':
        addLine(`  Pairs: ${status.strategy.pairs.join(', ') || 'default'}`)
        addLine(`  Direction: ${status.strategy.direction} | Conf≥${status.strategy.confidence_threshold}`)
        addLine(`  Max trades: ${status.strategy.max_trades}`)
        addLine('')
        break

      case '/balance':
        addLine(status.balance ? `  Balance: $${status.balance.toFixed(2)}` : '  Balance: N/A')
        addLine('')
        break

      case '/stop':
        addLine('  Exiting...')
        setTimeout(() => exit(), 500)
        break

      default:
        addLine(`  Unknown command: ${trimmed}`)
        addLine('')
    }
  }, [baseUrl, status, signals, addLine, exit])

  useInput((input, key) => {
    if (input === 'q' || input === 'Q') {
      exit()
    }
  })

  const onInputSubmit = (value: string) => {
    handleCommand(value)
    setInputKey(k => k + 1)
  }

  const mode = modeLabel(status.mode)
  const mColor = modeColor(status.mode)
  const ds = status.debate_stats
  const width = process.stdout.columns || 120

  // Header
  const headerLeft = `⚡ Aethera v1.5  [${mode}]  Scan #${status.scan_count}`
  const headerRight = status.balance ? `$${status.balance.toFixed(2)}` : '---'
  const connStatus = wsConnected ? '●' : '○'
  const connColor = wsConnected ? 'green' : 'red'

  // Signals table header
  const sigHeader = `  Symbol       Signal  Conf  Score  Regime     Reason`
  const sigDivider = `  ${'─'.repeat(12)} ${'─'.repeat(6)} ${'─'.repeat(4)} ${'─'.repeat(5)} ${'─'.repeat(8)} ${'─'.repeat(25)}`

  // Signal rows
  const sigRows = signals.slice(0, 12).map(s => {
    const debate = s.debate_overrode ? ` ⚡${s.debate_signal}` : ''
    const reason = (s.reasons?.[0] || '').slice(0, 25)
    return `  ${s.symbol.padEnd(12)} ${s.signal.padEnd(5)} ${String(s.confidence).padStart(4)}  ${s.composite_score.toFixed(1).padStart(5)}  ${s.regime.padEnd(8)} ${reason}${debate}`
  })

  // Summary line
  const longs = signals.filter(s => s.signal === 'LONG').length
  const shorts = signals.filter(s => s.signal === 'SHORT').length
  const waits = signals.filter(s => s.signal === 'WAIT').length
  const summary = `  ${longs}L / ${shorts}S / ${waits}W  |  Total: ${signals.length}`

  // Debate stats sidebar
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

  // Terminal lines (last 8)
  const termLines = terminalLines.slice(-8)

  return (
    <Box flexDirection="column" width={width}>
      {/* Header */}
      <Box>
        <Text bold color="cyan">{headerLeft}</Text>
        <Text>  </Text>
        <Text color={connColor}>{connStatus}</Text>
        <Text>  </Text>
        <Text color={mColor} bold>{headerRight}</Text>
        {ds.total > 0 && <Text>  Debate:{ds.total}</Text>}
      </Box>

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

      {/* Terminal */}
      <Box marginTop={1} flexDirection="column">
        <Text bold color="cyan">Terminal</Text>
        <Box borderStyle="single" borderColor="gray" paddingX={1} width={width - 2}>
          <Box flexDirection="column" width={width - 4}>
            {termLines.map((l, i) => {
              let color: string | undefined
              if (l.startsWith('>')) color = 'cyan'
              else if (l.includes('Error') || l.includes('failed')) color = 'red'
              else if (l.includes('triggered') || l.includes('complete')) color = 'green'
              return <Text key={i} color={color}>{l}</Text>
            })}
          </Box>
        </Box>
      </Box>

      {/* Input */}
      {showInput && (
        <Box marginTop={1}>
          <Text color="green">❯ </Text>
          <TextInput
            key={inputKey}
            onSubmit={onInputSubmit}
            suggestions={COMMANDS}
            placeholder="Type /help for commands..."
          />
        </Box>
      )}

      {/* Footer */}
      <Box marginTop={1}>
        <Text color="gray">q=quit  /status  /signals  /scan  /debate  /strategy  /help</Text>
      </Box>
    </Box>
  )
}
