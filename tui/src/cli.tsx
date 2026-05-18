#!/usr/bin/env node
import React from 'react'
import { render } from 'ink'
import meow from 'meow'
import App from './App.js'

const cli = meow(`
  Usage
    $ aethera-tui [options]

  Options
    --base-url    Backend API URL (default: http://127.0.0.1:8000)
    --help        Show help
    --version     Show version

  Examples
    $ aethera-tui
    $ aethera-tui --base-url http://192.168.1.100:8000
`, {
  importMeta: import.meta,
  flags: {
    baseUrl: {
      type: 'string',
      default: 'http://127.0.0.1:8000',
    },
  },
})

render(<App baseUrl={cli.flags.baseUrl} />)
