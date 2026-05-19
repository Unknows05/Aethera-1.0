import { commands } from './index.js'

export { commands }

commands['/help'] = (ctx) => {
  ctx.addLine('  Commands:')
  ctx.addLine('    /status    — Show system status')
  ctx.addLine('    /signals   — Show latest signals')
  ctx.addLine('    /scan      — Trigger manual scan')
  ctx.addLine('    /debate    — Show debate stats')
  ctx.addLine('    /strategy  — Show LLM strategy')
  ctx.addLine('    /balance   — Show balance')
  ctx.addLine('    /positions — Show open positions')
  ctx.addLine('    /model     — Show LLM model info')
  ctx.addLine('    /target    — Show capital target')
  ctx.addLine('    /trade     — Show trade status')
  ctx.addLine('    /swarm     — Swarm connection status')
  ctx.addLine('    /audit     — Verify audit chain')
  ctx.addLine('    /vault     — Search vault')
  ctx.addLine('    /skills    — List skills')
  ctx.addLine('    /memory    — View memory')
  ctx.addLine('    /stop      — Exit TUI')
  ctx.addLine('')
}

commands['/status'] = (ctx) => {
  const s = ctx.status
  const mode = s.mode.includes('TRADE') ? 'LIVE[TRADE]' : s.mode.includes('SIGNALS') ? 'LIVE[SIGNALS]' : s.mode
  ctx.addLine(`  Mode: ${mode} | Balance: ${s.balance ? '$' + s.balance.toFixed(2) : 'N/A'}`)
  ctx.addLine(`  Scans: ${s.scan_count} | Last: ${s.last_scan}`)
  ctx.addLine(`  Strategy: ${s.strategy.pairs.length} pairs | Dir: ${s.strategy.direction} | Conf≥${s.strategy.confidence_threshold}`)
  ctx.addLine(`  WebSocket: ${ctx.wsConnected ? 'connected' : 'disconnected'}`)
  ctx.addLine('')
}

commands['/signals'] = (ctx) => {
  const s = ctx.signals
  ctx.addLine(`  ${s.length} signals in last scan`)
  for (const sig of s.slice(0, 8)) {
    const debate = sig.debate_overrode ? ` [⚡${sig.debate_signal}]` : ''
    ctx.addLine(`    ${sig.symbol.padEnd(12)} ${sig.signal.padEnd(5)} conf=${String(sig.confidence).padStart(2)} score=${sig.composite_score.toFixed(1).padStart(5)} ${sig.regime}${debate}`)
  }
  ctx.addLine('')
}

commands['/balance'] = (ctx) => {
  ctx.addLine(ctx.status.balance ? `  Balance: $${ctx.status.balance.toFixed(2)}` : '  Balance: N/A')
  ctx.addLine('')
}

commands['/strategy'] = (ctx) => {
  const s = ctx.status.strategy
  ctx.addLine(`  Pairs: ${s.pairs.join(', ') || 'default'}`)
  ctx.addLine(`  Direction: ${s.direction} | Conf≥${s.confidence_threshold}`)
  ctx.addLine(`  Max trades: ${s.max_trades}`)
  ctx.addLine('')
}

commands['/debate'] = (ctx) => {
  const d = ctx.status.debate_stats
  if (d.total > 0) {
    ctx.addLine(`  Debates: ${d.total} | L:${d.longs} S:${d.shorts} W:${d.waits} | Avg conf: ${d.avg_confidence.toFixed(0)}% | Overrides: ${d.overrides}`)
  } else {
    ctx.addLine('  No debates yet (signals need conf >= 50)')
  }
  ctx.addLine('')
}

commands['/positions'] = (ctx) => {
  ctx.addLine('  Open positions:')
  ctx.addLine('  (fetching from API...)')
  ctx.addLine('')
}

commands['/model'] = (ctx) => {
  ctx.addLine('  LLM Model:')
  ctx.addLine('    Provider:  OpenRouter')
  ctx.addLine('    Model:     deepseek/deepseek-chat-v4:free')
  ctx.addLine('    Base URL:  https://openrouter.ai/api/v1')
  ctx.addLine('    Type /help for available commands to change model')
  ctx.addLine('')
}

commands['/target'] = (ctx) => {
  const s = ctx.status
  const bal = s.balance || 0
  ctx.addLine('  Capital Target:')
  ctx.addLine(`    Balance:    $${bal.toFixed(2)}`)
  ctx.addLine(`    Risk tier:  ${s.mode}`)
  ctx.addLine(`    Max trades: ${s.strategy.max_trades}/day`)
  ctx.addLine(`    Direction:  ${s.strategy.direction}`)
  ctx.addLine('')
}

commands['/trade'] = (ctx) => {
  const s = ctx.status
  if (s.mode.includes('TRADE')) {
    ctx.addLine('  Trading: LIVE')
    ctx.addLine(`    Max trades: ${s.strategy.max_trades}/day`)
    ctx.addLine(`    Mode: auto-execute with balance-based rules`)
  } else if (s.mode === 'DRY-RUN') {
    ctx.addLine('  Trading: DRY-RUN (signals only, no orders)')
  } else {
    ctx.addLine('  Trading: OFFLINE')
  }
  ctx.addLine('')
}

commands['/swarm'] = async (ctx) => {
  ctx.addLine('  Fetching swarm status...')
  try {
    const ky = (await import('ky')).default
    const api = ky.create({ prefixUrl: ctx.baseUrl, timeout: 5000, retry: { limit: 0 } })
    const data: any = await api.get('api/swarm/status').json()
    if (data.ok && data.swarm) {
      if (data.swarm.connected) {
        ctx.addLine(`  Swarm: [green]Connected[/] — ${data.swarm.agents} agents, ${data.swarm.lessons} lessons`)
        ctx.addLine(`    Server: ${data.swarm.server_url}`)
      } else {
        ctx.addLine(`  Swarm: [dim]${data.swarm.reason || 'Disconnected'}[/]`)
      }
    } else {
      ctx.addLine('  Swarm: [dim]Not configured[/]')
    }
  } catch {
    ctx.addLine('  Swarm: [dim]Cannot reach server[/]')
  }
  ctx.addLine('')
}

commands['/audit'] = (ctx) => {
  ctx.addLine('  Audit Chain:')
  ctx.addLine('  (audit chain verification via /api/audit does not exist)')
  ctx.addLine('')
}

commands['/vault'] = (ctx) => {
  ctx.addLine('  Vault:')
  ctx.addLine('    vault/skills/     — Auto-created trading skills')
  ctx.addLine('    vault/lessons/    — Per-trade lessons')
  ctx.addLine('    vault/memory/     — Bounded agent memory')
  ctx.addLine('    vault/strategies/ — User-defined strategies')
  ctx.addLine('    Use CLI: aethera vault search <query>')
  ctx.addLine('')
}

commands['/skills'] = (ctx) => {
  ctx.addLine('  Skills:')
  ctx.addLine('    (skills are auto-created from 3+ similar trade outcomes)')
  ctx.addLine('    Use CLI: aethera vault list --folder skills')
  ctx.addLine('')
}

commands['/memory'] = (ctx) => {
  ctx.addLine('  Memory:')
  ctx.addLine('    (bounded agent memory in vault/memory/MEMORY.md)')
  ctx.addLine('    Max size: 2,200 characters')
  ctx.addLine('')
}

commands['/stop'] = (ctx) => {
  ctx.addLine('  Exiting...')
  setTimeout(() => ctx.exit(), 500)
}
