import React from 'react'
import { Box, Text } from 'ink'

interface ModalProps {
  title: string
  visible: boolean
  children: React.ReactNode
  width: number
}

export default function Modal({ title, visible, children, width }: ModalProps) {
  if (!visible) return null

  const modalWidth = Math.min(width - 4, 60)
  const padX = Math.max(0, Math.floor((width - modalWidth) / 2))

  return (
    <Box flexDirection="column" paddingLeft={padX} width={modalWidth}>
      <Box borderStyle="double" borderColor="cyan" paddingX={2} paddingY={1} width={modalWidth}>
        <Box flexDirection="column" width={modalWidth - 4}>
          <Text bold color="cyan">{title}</Text>
          {children}
        </Box>
      </Box>
    </Box>
  )
}
