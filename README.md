# Aethera v1.5.8

**Autonomous AI crypto trading agent — Binance Futures. Self-hosted, self-learning.**

- Market: Binance Futures USDT-M Perpetual (LONG + SHORT)
- AI: OpenRouter (DeepSeek V4, free models available)
- Interface: TypeScript TUI (terminal) + Web dashboard (:8000)
- Daemon: Autonomous 24/7 — screening, debate, management, reflection

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Unknows05/Aethera-1.0/main/install.sh | bash
```

Requirements: Python 3.10+, Node.js 18+

---

## Quick Start

```bash
aethera init       # Setup wizard (Binance API, LLM model, target, identity)
aethera start      # Launch TUI + API server
```

Setup wizard steps: Binance API key → LLM provider/model → daily target/max trades → Telegram (optional) → Identity generated. Ends with "Start now? [Y/n]".

---

## Autonomous Mode (Daemon)

```bash
aethera daemon start     # Start 24/7 background trading
aethera daemon stop      # Graceful shutdown
aethera daemon status    # Check state
aethera daemon logs      # View logs
aethera daemon restart   # Restart
```

**Cycles:** Screening (15min) → Management (5min) → Reflection (60min) → Health check (1min). Survives reboot, auto-recovers from crash.

---

## CLI Commands

### Core
| Command | Description |
|---------|-------------|
| `aethera init` | Setup wizard |
| `aethera start` | TUI + API server |
| `aethera stop` | Stop all processes |
| `aethera status` | Account overview (balance, tier, model) |
| `aethera ready` | System readiness check |
| `aethera doctor` | Full diagnostic |
| `aethera update` | Auto-update from GitHub |

### Trading
| Command | Description |
|---------|-------------|
| `aethera positions` | Open positions |
| `aethera signals` | Latest signals |
| `aethera stats --days 30` | Performance (WR, PF, Sharpe) |
| `aethera backtest` | Run backtest |
| `aethera lessons` | Recent trade lessons |

### Target & Risk
| Command | Description |
|---------|-------------|
| `aethera target show` | Current target |
| `aethera target set 15` | Set daily target % |
| `aethera target risk show` | Risk parameters |
| `aethera target risk set trade 5` | Risk per trade % |
| `aethera target mode aggressive` | Set risk preset |

### Vault
| Command | Description |
|---------|-------------|
| `aethera vault search <query>` | FTS5 search skills/lessons |
| `aethera vault list` | List vault files |
| `aethera vault backup` | Backup to tar.gz |
| `aethera vault restore <file>` | Restore from backup |
| `aethera vault cleanup` | Remove old backups |

### Swarm
| Command | Description |
|---------|-------------|
| `aethera swarm status` | Connection status |
| `aethera swarm connect <url>` | Connect to swarm server |
| `aethera swarm push` | Push anonymous lessons |
| `aethera swarm pull` | Pull crowd lessons |

### Config
| Command | Description |
|---------|-------------|
| `aethera config show` | Show settings |
| `aethera model` | Browse LLM models |
| `aethera identity show` | Agent ID + swarm status |
| `aethera audit verify` | Verify audit chain integrity |
| `aethera cleanup` | Clean old logs |

---

## TUI Commands (inside `aethera start`)

```
/status  /signals  /scan  /debate  /strategy  /balance  /stop
/model   /target  /trade  /swarm  /audit  /vault  /skills  /memory  /positions
/help    q=quit
```

---

## Architecture

```
cli.py ── aethera init    → Setup wizard → "Start now?" → TUI
       ├─ aethera start   → API server (bg) + TypeScript TUI (fg)
       └─ aethera daemon  → Background autonomous process

Agents:
  ScreeningAgent  (15min) — Scan 500+ coins → LLM select pairs → Bull/Bear debate
  ManagementAgent (5min)  — Check positions → STAY/CLOSE/MOVE_SL/ADD via LLM
  ReflectionAgent (60min) — Learn from outcomes → create skills → evolve → sync swarm
  HealthMonitor    (1min)  — Self-healing, auto-restart, alerts

Engine:
  engine_v2.py — Deterministic scoring, ML filter, debate pipeline, risk gate
  vault/       — Knowledge base (FTS5 index, skills, lessons, memory)
```

---

## Security

| Feature | Implementation |
|---------|---------------|
| API keys | Fernet AES-128-CBC encrypted, chmod 600 |
| Passwords | bcrypt 12-round (fallback PBKDF2-HMAC-SHA256 600K) |
| Audit chain | HMAC-SHA256 tamper-evident log |
| Swarm identity | Ed25519 signed requests |
| PnL to swarm | Anonymized — direction only (WIN/LOSS), no amounts |
| CORS | Localhost only |
| .env | Quote handling + injection guard |
| Risk gates | Hard rules (code-enforced), soft rules (LLM-overridable with audit) |

## Safety Nets

| Scenario | Response |
|----------|----------|
| Drawdown >20% | Emergency stop — close all positions |
| 3 consecutive losses | Circuit breaker — pause 4 hours |
| LLM unavailable | Fallback to quant-only screening |
| Binance API down | Pause trading, retry every 5min |
| Disk full | Safe mode — close positions, stop |
| Crash | Auto-restart with backoff (10s, 30s, 60s, 5min) |

---

## Dev Commands

```bash
cd tui && npm run dev      # TUI dev mode (hot reload)
cd tui && npm run build    # Build TUI to dist/

# Verify system
python3 -c "from src.agents import *; print(len(__all__), 'agents')"
python3 -m pytest tests/ -v
```
