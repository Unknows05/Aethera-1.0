import React from 'react'
import { Box, Text } from 'ink'

interface TranscriptProps {
  lines: string[]
  width: number
}

export default function Transcript({ lines, width }: TranscriptProps) {
  return (
    <Box marginTop={1} flexDirection="column">
      <Text bold color="cyan">Terminal</Text>
      <Box borderStyle="single" borderColor="gray" paddingX={1} width={width - 2}>
        <Box flexDirection="column" width={width - 4}>
          {lines.map((l, i) => {
            let color: string | undefined
            if (l.startsWith('>')) color = 'cyan'
            else if (l.includes('Error') || l.includes('failed')) color = 'red'
            else if (l.includes('triggered') || l.includes('complete')) color = 'green'
            return <Text key={i} color={color}>{l}</Text>
          })}
        </Box>
      </Box>
    </Box>
  )
}
