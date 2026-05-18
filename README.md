# Aethera v1.5

**Autonomous AI crypto trading agent. Self-hosted. Self-learning. Swarm-intelligent.**

> Upgraded from Coin-Screener v1.3 → Aethera v1.5  
> Previous repo: [Aethera-1.0](https://github.com/Unknows05/Aethera-1.0)

---

## What is Aethera?

Aethera is an autonomous trading agent that connects to your Binance Futures account. An LLM acts as the strategist — it analyzes the market, picks pairs, and configures risk. A quant engine scans markets in parallel, generates signals, and executes trades. A Bull/Bear debate pipeline reasons adversarially before each decision. A knowledge vault stores lessons and skills from past trades. Agents can share anonymous patterns via swarm learning.

- **Market:** Binance Futures USDT-M Perpetual (LONG + SHORT)
- **AI:** OpenRouter (DeepSeek V4, free models available)
- **Interface:** TypeScript TUI (Hermes-style terminal dashboard)
- **Learning:** Real PnL outcomes → vault lessons → auto-skills → swarm sync
- **Safety:** Hard risk gates, circuit breakers, encrypted keys

---

## Install

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/Unknows05/Aethera/main/install.sh | bash

# Windows
irm https://raw.githubusercontent.com/Unknows05/Aethera/main/install.ps1 | iex
```

### Update from Aethera v1.0

If you have the previous version installed:

```bash
aethera update          # Auto-update to v1.5
# OR manually:
cd ~/aethera && git pull origin main
cd tui && npm install && npm run build
```

Your `.env`, `data/`, and `vault/` are preserved during update.

---

## Quick Start

```bash
aethera init       # One-time setup wizard
aethera start      # Launch TUI + API server
```

Setup wizard guides you through:
1. **Binance API** — key + secret (Futures enabled, IP whitelisted)
2. **LLM Model** — pick from OpenRouter (free tier available)
3. **Daily Target** — profit target %, max trades, risk per trade
4. **Telegram** — optional notifications
5. **Identity** — auto-generated agent ID

At the end, it asks: **"Start Aethera now? [Y/n]"** → press Y to launch.

---

## Commands

### Core

| Command | Description |
|---------|-------------|
| `aethera init` | Full interactive setup wizard |
| `aethera start` | Launch API server + TypeScript TUI |
| `aethera stop` | Stop all Aethera processes |
| `aethera restart` | Stop and restart |
| `aethera status` | Check balance, tier, model |
| `aethera ready` | System readiness check |
| `aethera doctor` | Full diagnostic |

### Trading

| Command | Description |
|---------|-------------|
| `aethera positions` | Show open positions from Binance |
| `aethera signals` | Latest signals from database |
| `aethera stats --days 30` | Performance summary (WR, PF, Sharpe) |
| `aethera backtest` | Run backtest on historical signals |
| `aethera lessons` | Recent trading lessons |

### Target & Risk

| Command | Description |
|---------|-------------|
| `aethera target show` | Current target config |
| `aethera target set 15` | Set daily target % |
| `aethera target risk show` | Risk parameters |
| `aethera target risk set trade 5` | Risk per trade % |
| `aethera target compound on` | Enable compounding |

### Daemon (Autonomous Mode)

| Command | Description |
|---------|-------------|
| `aethera daemon start` | Start background daemon |
| `aethera daemon stop` | Stop daemon |
| `aethera daemon status` | Check daemon state |
| `aethera daemon logs` | View daemon logs |
| `aethera daemon restart` | Restart daemon |

### Knowledge Vault

| Command | Description |
|---------|-------------|
| `aethera memory show` | View agent memory |
| `aethera log` | Search decision log |
| `aethera log -s BTCUSDT` | Search by symbol |

### Swarm

| Command | Description |
|---------|-------------|
| `aethera swarm status` | Swarm connection status |
| `aethera swarm connect <url>` | Connect to swarm server |
| `aethera swarm disconnect` | Disconnect from swarm |

### Config

| Command | Description |
|---------|-------------|
| `aethera config show` | Show current .env values |
| `aethera model` | Browse and change LLM model |
| `aethera model -s deepseek` | Filter models by name |
| `aethera cleanup` | Clean old logs and sessions |
| `aethera export` | Export trade data |
| `aethera uninstall` | Remove Aethera completely |

---

## TUI Commands

Inside the TypeScript TUI, type `/` followed by a command:

| Command | Description |
|---------|-------------|
| `/status` | System status + balance |
| `/signals` | Latest signals list |
| `/scan` | Trigger manual scan |
| `/debate` | Debate pipeline stats |
| `/strategy` | Current LLM strategy |
| `/balance` | Check balance |
| `/help` | List all commands |
| `/stop` | Exit TUI |

Press `q` to quit. Tab completion works for commands.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    TypeScript TUI (Ink)                      │
│  Signals panel  │  Status sidebar  │  Terminal  │  Footer   │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP polling (3s)
┌────────────────────────▼────────────────────────────────────┐
│                    FastAPI Server (:8000)                    │
│  /api/status  /api/signals  /api/tui/status  /api/scan     │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                  Screening Engine V2                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Quick    │→ │ Deep     │→ │ Debate   │→ │ Risk Gate  │  │
│  │ Scan     │  │ Scan     │  │ Pipeline │  │ (Hard+Soft)│  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    Knowledge Vault                           │
│  Skills  │  Lessons  │  Memory  │  FTS5 Index  │  Backup   │
└─────────────────────────────────────────────────────────────┘
```

### Debate Pipeline

For signals with confidence ≥ 50:

1. **Bull Agent** — argues LONG case (TA, OI, funding, regime, vault knowledge)
2. **Bear Agent** — argues SHORT case (counter-evidence, risks, divergence)
3. **3 rounds** of opening → rebuttal → final arguments
4. **Synthesizer** — neutral LLM weighs both sides, outputs final signal
5. **Risk Gate** — hard rules (no override) + soft rules (LLM can override with reason)

### Risk Gates

**Hard Rules (cannot override):**
- Max drawdown ≥ 20% → circuit breaker
- 3 consecutive losses → pause 4 hours
- Blacklisted symbols blocked
- Max daily trades reached

**Soft Rules (LLM can override):**
- Regime conflict (LONG in BEAR regime)
- High funding rate
- Low confidence signal
- High correlation exposure

---

## Project Structure

```
aethera/
├── cli.py                  # CLI entry point (click)
├── api.py                  # FastAPI server
├── config.yaml             # Default configuration
├── README.md               # This file
├── tui/                    # TypeScript TUI (Ink + React)
│   ├── src/
│   │   ├── cli.tsx         # CLI entry
│   │   ├── App.tsx         # Main TUI component
│   │   ├── api.ts          # API client
│   │   └── types.ts        # TypeScript interfaces
│   └── dist/               # Built output
├── src/
│   ├── engine_v2.py        # Screening engine
│   ├── daemon.py           # Background daemon
│   ├── agents/
│   │   ├── bull_agent.py   # Bull debate agent
│   │   ├── bear_agent.py   # Bear debate agent
│   │   ├── synthesizer.py  # Debate synthesizer
│   │   ├── debate.py       # Debate orchestrator
│   │   ├── risk_gate.py    # Risk gate
│   │   ├── scheduler.py    # Cycle scheduler
│   │   ├── health.py       # Health monitor
│   │   └── data_collector.py
│   ├── vault/
│   │   ├── indexer.py      # FTS5 indexer
│   │   ├── skill_manager.py
│   │   ├── lesson_manager.py
│   │   ├── memory.py
│   │   ├── search.py
│   │   └── backup.py
│   └── ...                 # Other modules
└── data/                   # Runtime data (DB, logs, state)
```

---

## FAQ

**Is it free?** Yes. You pay only Binance trading fees + LLM API (free tier on OpenRouter).

**Is it safe?** API keys are encrypted locally. Code runs on your machine. Swarm shares only anonymous patterns (no PnL amounts).

**Minimum capital?** $10 works. $25+ recommended.

**Can I lose money?** YES. Crypto trading is high-risk. This is a tool, not a guarantee. Never trade more than you can afford to lose.

**What if LLM API fails?** Falls back to quant-only mode. No LLM = no debate, but quant engine still runs.

**Does it work 24/7?** Yes. Use `aethera daemon start` for autonomous mode. Survives TUI close and reboot.

---

## License

MIT

---

## Disclaimer

This software is for educational purposes only. Cryptocurrency trading carries significant risk. Past performance does not guarantee future results. The authors are not responsible for any financial losses. Use at your own risk.
