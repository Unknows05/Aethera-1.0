import React from 'react'
import { Box, Text } from 'ink'
import { TextInput } from '@inkjs/ui'

interface CommandInputProps {
  onSubmit: (value: string) => void
  inputKey: number
}

const COMMANDS = ['/status', '/signals', '/scan', '/debate', '/strategy', '/balance', '/help', '/stop',
  '/model', '/target', '/trade', '/swarm', '/audit', '/vault', '/skills', '/memory', '/positions']

export default function CommandInput({ onSubmit, inputKey }: CommandInputProps) {
  return (
    <Box marginTop={1}>
      <Text color="green">❯ </Text>
      <TextInput
        key={inputKey}
        onSubmit={onSubmit}
        suggestions={COMMANDS}
        placeholder="Type /help for commands..."
      />
    </Box>
  )
}
