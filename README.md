# Aethera — Autonomous AI Trading Agent

> Local-first crypto trading agent with swarm intelligence.
> Runs on your machine. Your data stays yours.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/Aethera.git
cd Aethera

# 2. Install
./install.sh

# 3. Setup
aethera init

# 4. Run
aethera start
```

## What Is Aethera?

Aethera is an **autonomous trading agent** that:
- **Scans** 500+ crypto pairs on Binance Futures
- **Analyzes** markets with 50+ technical indicators + LLM reasoning
- **Debates** trade decisions with Bull/Bear agents before executing
- **Learns** from every trade outcome
- **Shares** anonymized lessons with the HiveMind swarm network
- **Improves** by pulling crowd-optimized strategies

All analysis, execution, and data storage happens **on your machine**. Only anonymized lessons (no PnL, no prices) are shared with the swarm.

## Architecture

```
YOUR MACHINE (user-side)                    SWARM HUB (server-side)
├── cli.py → aethera init/start/daemon      ├── hivemind.aethera-s1.com
├── api.py → FastAPI :8000                  │   ├── swarm_server.py :8900
├── src/engine_v2.py                        │   ├── lessons push/pull
├── src/agents/                             │   ├── skills push/pull
│   ├── screening_agent.py (5 min)          │   ├── performance tracking
│   ├── management_agent.py (5 min)         │   └── crowd thresholds
│   ├── bull/bear/debate (3 rounds)         │
│   ├── risk_gate.py                        └── DB: swarm.db (SQLite)
│   ├── scheduler.py
│   └── reflection.py (60 min)
├── src/vault/ (knowledge vault)
├── src/hivemind_client.py → auto-connect
├── tui/ (TypeScript terminal UI)
└── vault/ (Markdown skills/lessons)
```

## Project Structure

```
├── cli.py              # CLI commands (aethera init/start/status/daemon)
├── api.py              # FastAPI server + background scheduler
├── config.yaml         # Default configuration
├── install.sh          # Setup script (Linux/macOS)
├── install.ps1         # Setup script (Windows)
├── requirements.txt    # Python dependencies
├── requirements-ml.txt # ML dependencies (XGBoost, etc.)
│
├── src/                # Core engine
│   ├── engine_v2.py    # Main screening engine
│   ├── hivemind_client.py  # Swarm client (auto-connect)
│   ├── identity.py     # Ed25519 keypair for swarm auth
│   ├── agents/         # Multi-agent system
│   │   ├── screening_agent.py  # Market scanner
│   │   ├── management_agent.py # Position manager
│   │   ├── bull_agent.py       # Bull case arguments
│   │   ├── bear_agent.py       # Bear case arguments
│   │   ├── debate.py           # 3-round debate orchestrator
│   │   ├── risk_gate.py        # Hard risk rules
│   │   ├── scheduler.py        # Job scheduler
│   │   ├── reflection.py       # Self-reflection (60 min)
│   │   └── health.py           # System health monitor
│   ├── vault/          # Knowledge vault (FTS5 search)
│   │   ├── indexer.py      # FTS5 SQLite index
│   │   ├── skill_manager.py    # Skills CRUD
│   │   ├── lesson_manager.py   # Lessons CRUD
│   │   ├── memory.py       # Persistent agent memory
│   │   ├── search.py       # Full-text search
│   │   └── backup.py       # Vault backup
│   ├── binance_api.py  # Binance Futures API wrapper
│   ├── risk_manager.py # Risk calculations
│   ├── scorer.py       # Technical scoring
│   ├── market_regime.py    # Regime detection
│   ├── ml_engine.py    # ML predictions
│   ├── backtest_engine.py  # Backtesting
│   ├── telegram_bot.py # Telegram notifications
│   └── ...
│
├── tui/                # TypeScript terminal UI (React + Ink)
│   ├── src/
│   └── dist/cli.js     # Built output
│
├── vault/              # Knowledge base (Markdown files)
│   ├── skills/         # Trading strategies
│   ├── lessons/        # Trade lessons
│   ├── strategies/     # Strategy configs
│   └── memory/         # Agent memory
│
├── static/             # Web dashboard (HTML + HTMX + Alpine.js)
├── scripts/            # Utility scripts
└── tests/              # Test suite
```

## Commands

### Setup

```bash
# Install dependencies
./install.sh            # Linux/macOS
./install.ps1           # Windows

# Interactive setup wizard
aethera init
# → Sets up .env, generates Ed25519 identity, tests connection
```

### Run

```bash
# Start API server + TUI
aethera start

# Stop all processes
aethera stop

# Restart
aethera restart

# Run as background daemon (24/7)
aethera daemon start
aethera daemon status
aethera daemon logs

# Web dashboard only
aethera dashboard --port 8000
```

### Diagnostics

```bash
# Full system diagnostic
aethera doctor

# Readiness check
aethera ready

# Show status (balance, tier, model)
aethera status
```

### Trading

```bash
# View open positions
aethera positions

# View latest signals
aethera signals

# Performance summary
aethera stats --days 30

# Run backtest
aethera backtest --capital 25
```

### Knowledge Vault

```bash
# View agent memory
aethera memory show

# View decision log
aethera log

# Search by symbol
aethera log -s BTCUSDT

# View recent lessons
aethera lessons
```

### Swarm (HiveMind)

```bash
# Check swarm connection
aethera swarm status

# Set custom swarm server
aethera swarm connect https://hivemind.aethera-s1.com

# Push lessons to swarm
aethera swarm push

# Pull lessons from swarm
aethera swarm pull --regime BULL
```

### Configuration

```bash
# Show .env values (secrets hidden)
aethera config show

# Browse and change LLM model
aethera model
aethera model -s deepseek    # Filter models
```

## Configuration

### .env

Created by `aethera init`. Override defaults from `config.yaml`.

```bash
# Exchange API
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret

# LLM (OpenRouter)
OPENROUTER_API_KEY=your_key
DEFAULT_MODEL=deepseek/deepseek-chat-v4

# HiveMind Swarm
HIVEMIND_URL=https://hivemind.aethera-s1.com
HIVEMIND_AGENT_ID=auto-generated

# Risk
MAX_DRAWDOWN_PCT=20
MAX_POSITION_PCT=5
CONSECUTIVE_LOSS_PAUSE=4
```

### config.yaml

Default values. `.env` overrides these.

## Security

- **Ed25519 identity** — each agent has a unique keypair for swarm auth
- **PnL anonymization** — all data pushed to swarm is stripped of amounts, prices, percentages
- **SSRF guard** — hivemind_client blocks localhost/private IPs
- **API keys encrypted** — Fernet AES-256, stored in `data/`
- **Risk hard-gates** — code-enforced, no LLM override:
  - Max drawdown ≥ 20% → circuit breaker
  - 3 consecutive losses → pause 4 hours
  - Max position: 5% equity
  - Blacklisted symbols blocked

## HiveMind Swarm

Connect to `hivemind.aethera-s1.com` to:
- **Push** anonymized lessons from your trades
- **Pull** crowd-optimized thresholds and proven skills
- **View** live swarm stats at https://hivemind.aethera-s1.com

Your raw data **never leaves your machine**. Only distilled, anonymized knowledge is shared.

## Requirements

- Python 3.10+
- Node.js 18+ (for TUI)
- Binance Futures account with API keys
- OpenRouter API key (for LLM)

## Links

- **Swarm Dashboard**: https://hivemind.aethera-s1.com
- **Landing Page**: https://aethera-s1.com
- **HiveMind API**: https://hivemind.aethera-s1.com/api/hivemind/health

