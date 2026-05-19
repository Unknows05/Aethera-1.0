import React from 'react'
import { Box, Text } from 'ink'
import type { SystemStatus } from '../types.js'

interface HeaderProps {
  status: SystemStatus
  wsConnected: boolean
}

export function modeLabel(mode: string): string {
  if (mode.includes('TRADE')) return 'LIVE[TRADE]'
  if (mode.includes('SIGNALS')) return 'LIVE[SIGNALS]'
  if (mode === 'DRY-RUN') return 'DRY-RUN'
  return 'OFFLINE'
}

export function modeColor(mode: string): string {
  if (mode.includes('TRADE') || mode.includes('SIGNALS')) return 'green'
  if (mode === 'DRY-RUN') return 'yellow'
  return 'red'
}

export default function Header({ status, wsConnected }: HeaderProps) {
  const mode = modeLabel(status.mode)
  const mColor = modeColor(status.mode)
  const headerLeft = `⚡ Aethera v1.5  [${mode}]  Scan #${status.scan_count}`
  const headerRight = status.balance ? `$${status.balance.toFixed(2)}` : '---'
  const connStatus = wsConnected ? '●' : '○'
  const connColor = wsConnected ? 'green' : 'red'
  const ds = status.debate_stats

  return (
    <Box>
      <Text bold color="cyan">{headerLeft}</Text>
      <Text>  </Text>
      <Text color={connColor}>{connStatus}</Text>
      <Text>  </Text>
      <Text color={mColor} bold>{headerRight}</Text>
      {ds.total > 0 && <Text>  Debate:{ds.total}</Text>}
    </Box>
  )
}
