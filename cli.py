"""
Aethera CLI — autonomous crypto trading agent.
"""
import sys
import os
import json
import time
import asyncio
import sqlite3
import platform
import signal
import subprocess
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.columns import Columns

console = Console()
sys.path.insert(0, str(Path(__file__).parent))

SCRIPT_DIR = Path(__file__).parent


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _env_load():
    e = {}
    p = Path(".env")
    if p.exists():
        for line in p.read_text().split("\n"):
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip()
    return e

def _env_apply():
    """Load .env into os.environ so modules using os.getenv() can read it."""
    env = _env_load()
    for k, v in env.items():
        if v and k not in os.environ:
            os.environ[k] = v


def _env_set(key, value):
    p = Path(".env")
    old_vars = _env_load()
    # Strip newlines and whitespace to prevent injection of extra env var lines
    old_vars[key] = value.strip().replace("\n", "").replace("\r", "")
    lines = []
    for k, v in old_vars.items():
        if v:
            lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)


def _env_unset(key):
    p = Path(".env")
    old_vars = _env_load()
    old_vars.pop(key, None)
    lines = []
    for k, v in old_vars.items():
        if v:
            lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)


def _public_ip():
    try:
        import urllib.request
        return urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode().strip()
    except Exception:
        return None


def _balance(api_key="", api_secret=""):
    if not api_key:
        env = _env_load()
        api_key = env.get("BINANCE_API_KEY", "")
        api_secret = env.get("BINANCE_API_SECRET", "")
    if not api_key:
        return None
    try:
        import ccxt
        ex = ccxt.binance({"apiKey": api_key, "secret": api_secret,
                           "options": {"defaultType": "future"}, "enableRateLimit": True})
        return float(ex.fetch_balance().get("USDT", {}).get("free", 0))
    except:
        return None


# Cached balance — only fetch every 60s to avoid Binance rate limit
_balance_cache = {"value": None, "time": 0}

def _balance_cached():
    if time.time() - _balance_cache["time"] < 60:
        return _balance_cache["value"]
    _balance_cache["value"] = _balance()
    _balance_cache["time"] = time.time()
    return _balance_cache["value"]


def _fetch_openrouter_models():
    import requests
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", timeout=15,
                         headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        data = r.json()
        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            pricing = m.get("pricing", {})
            pc = float(pricing.get("prompt", "0") or 0)
            cc = float(pricing.get("completion", "0") or 0)
            name = m.get("name", mid)
            ctx = m.get("context_length", 0)
            params = m.get("supported_parameters", [])
            if isinstance(params, list) and params and "tools" not in params:
                continue
            models.append((mid, name, pc, cc, ctx))
        models.sort(key=lambda x: (0 if x[2] + x[3] == 0 else 1, x[2] + x[3]))
        return models
    except Exception:
        return None


def _model_table(models):
    table = Table(title="Available Models — OpenRouter", border_style="dim")
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Model", style="cyan", max_width=48)
    table.add_column("In $/Mtok", style="green", justify="right")
    table.add_column("Out $/Mtok", style="green", justify="right")
    table.add_column("Context", style="dim", justify="right")

    for i, (mid, name, pc, cc, ctx) in enumerate(models[:30]):
        cin = "free" if pc == 0 else f"${pc * 1_000_000:.2f}"
        cout = "free" if cc == 0 else f"${cc * 1_000_000:.2f}"
        tag = ""
        if i == 0:
            tag = " \u2190 recommended"
        elif pc + cc == 0:
            tag = " (free)"
        ctx_str = f"{ctx // 1000}k" if ctx > 1000 else str(ctx) if ctx > 0 else "?"
        table.add_row(str(i + 1), f"{mid}{tag}", cin, cout, ctx_str)
    return table


def _tier_label(bal):
    if bal is None:
        return "UNKNOWN"
    if bal < 50:
        return "AGGRESSIVE"
    elif bal < 200:
        return "GROWTH"
    elif bal < 500:
        return "CONSERVATIVE"
    return "PRESERVATION"


def _require_env():
    if not Path(".env").exists():
        console.print("[red]Not configured. Run: [bold]aethera init[/bold][/red]")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# CLI GROUP
# ═══════════════════════════════════════════════════════════════

@click.group()
@click.version_option(version="1.5.0", prog_name="Aethera")
def cli():
    """Aethera V1.5 — Autonomous crypto trading agent. AI-powered, self-learning, swarm-intelligent."""


# =========================================================================
# SETUP COMMANDS
# =========================================================================

@cli.command()
def init():
    """Full interactive setup wizard."""
    console.print(Panel.fit("[bold cyan]Aethera v1.5 — Setup[/bold cyan]"))

    # Step 0: Public IP
    ip = _public_ip()
    if ip:
        console.print(f"\n[green]Your IP: {ip}[/green]")
        console.print("[dim]Whitelist this IP in Binance API settings.[/dim]")
    else:
        console.print("\n[yellow]Cannot detect IP. Whitelist your server IP in Binance.[/yellow]")

    # Step 1: Binance API
    console.print("\n[bold]1. Binance API[/bold]")
    console.print("[dim]Binance -> API Management -> [check] Enable Futures  [ ] Enable Withdrawals[/dim]")
    while True:
        api_key = click.prompt("API Key", default="", show_default=False, hide_input=True)
        if api_key.strip():
            break
        console.print("[yellow]Required.[/yellow]")
    while True:
        api_secret = click.prompt("Secret Key", default="", show_default=False, hide_input=True)
        if api_secret.strip():
            break
        console.print("[yellow]Required.[/yellow]")

    _env_set("BINANCE_API_KEY", api_key)
    _env_set("BINANCE_API_SECRET", api_secret)

    bal = _balance(api_key, api_secret)
    if bal is not None:
        tier = _tier_label(bal)
        console.print(f"[green]Balance: ${bal:.2f} — {tier}[/green]")
    else:
        console.print("[yellow]Could not verify balance.[/yellow]")
        console.print("[dim]Check: IP whitelisted? API key has Futures enabled? Internet OK?[/dim]")
        if not click.confirm("Continue with this API key anyway?", default=False):
            console.print("[dim]You can re-run 'aethera init' later or update .env manually.[/dim]")
            # Allow re-input
            console.print("\n[bold]Re-enter Binance API?[/bold]")
            if click.confirm("Try again?", default=True):
                while True:
                    api_key = click.prompt("API Key", default="", show_default=False, hide_input=True)
                    if api_key.strip(): break
                    console.print("[yellow]Required.[/yellow]")
                while True:
                    api_secret = click.prompt("Secret Key", default="", show_default=False, hide_input=True)
                    if api_secret.strip(): break
                    console.print("[yellow]Required.[/yellow]")
                _env_set("BINANCE_API_KEY", api_key)
                _env_set("BINANCE_API_SECRET", api_secret)
                bal = _balance(api_key, api_secret)
                if bal is not None:
                    console.print(f"[green]Balance: ${bal:.2f} — {_tier_label(bal)}[/green]")
                else:
                    console.print("[yellow]Still cannot verify. You can fix this later.[/yellow]")
        else:
            console.print("[dim]OK, continuing with unverified API. Run 'aethera status' later to check.[/dim]")

    # Step 2: LLM (mandatory — no skip)
    console.print("\n[bold]2. LLM Model (required)[/bold]")
    console.print("[dim]Aethera uses LLM as strategist. You need an API key.[/dim]")
    console.print("[dim]Get free key: openrouter.ai/keys[/dim]")

    prov = click.prompt("\n  Provider", type=click.Choice(["openrouter", "openai", "groq", "ollama"]),
                        default="openrouter")
    _env_set("LLM_PROVIDER", prov)

    # Fetch models FIRST — before asking API key
    if prov in ("openrouter", "openai", "groq"):
        console.print("\n  [dim]Fetching available models...[/dim]")
        models = _fetch_openrouter_models()
        if models:
            console.print(_model_table(models))
            console.print("  [dim]Pick by number or type model ID[/dim]")
            while True:
                choice = click.prompt("  Model", default="1").strip()
                if choice:
                    break
                console.print("  [yellow]Required. Pick a model from the list.[/yellow]")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(models):
                    mdl = models[idx][0]
                else:
                    console.print(f"  [yellow]#{choice} not in list. Using #1.[/yellow]")
                    mdl = models[0][0]
            except ValueError:
                matched = [m for m in models if m[0] == choice]
                if matched:
                    mdl = matched[0][0]
                else:
                    console.print(f"  [yellow]'{choice}' not found. Using #1.[/yellow]")
                    mdl = models[0][0]
        else:
            console.print("  [yellow]Cannot fetch models. Enter model ID manually.[/yellow]")
            while True:
                mdl = click.prompt("  Model ID", default="google/gemini-2.5-flash-lite:free").strip()
                if mdl:
                    break

        _env_set("LLM_MODEL", mdl)
        console.print(f"  [green]Model: {mdl}[/green]")

        # NOW ask for API key
        console.print(f"\n  [dim]API key needed for {mdl}. Get one at openrouter.ai/keys[/dim]")
        while True:
            llm_key = click.prompt("  API Key", default="", show_default=False, hide_input=True)
            if llm_key.strip():
                break
            console.print("  [yellow]API key is required. Cannot run without LLM.[/yellow]")

        if prov == "openrouter":
            _env_set("OPENROUTER_API_KEY", llm_key)
        else:
            _env_set("LLM_API_KEY", llm_key)

        # Test connection
        console.print("  [dim]Testing...[/dim]")
        try:
            from openai import OpenAI
            base = "https://openrouter.ai/api/v1" if prov == "openrouter" else \
                   "https://api.openai.com/v1" if prov == "openai" else \
                   "https://api.groq.com/openai/v1"
            c = OpenAI(base_url=base, api_key=llm_key, timeout=10)
            r = c.chat.completions.create(model=mdl, messages=[{"role": "user", "content": "OK"}], max_tokens=2)
            if r.choices:
                console.print("  [green]Connection OK[/green]")
        except Exception as e:
            console.print(f"  [yellow]Test failed: {e}[/yellow]")

    elif prov == "ollama":
        console.print("\n  [dim]Local models via Ollama. Make sure Ollama is running.[/dim]")
        while True:
            mdl = click.prompt("  Model name (e.g. qwen2.5:3b)", default="qwen2.5:3b").strip()
            if mdl:
                break
        _env_set("LLM_MODEL", mdl)
        _env_set("LLM_BASE_URL", click.prompt("  Ollama URL", default="http://localhost:11434/v1"))
        console.print(f"  [green]Model: {mdl} (local)[/green]")
        llm_key = ""

    # Set base URLs
    if prov == "openai":
        _env_set("LLM_BASE_URL", "https://api.openai.com/v1")
    elif prov == "groq":
        _env_set("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    else:
        _env_set("LLM_BASE_URL", "https://openrouter.ai/api/v1")

    if prov == "ollama":
        _env_set("LLM_BASE_URL", click.prompt("Ollama URL", default="http://localhost:11434/v1"))
        _env_set("LLM_MODEL", click.prompt("Model name", default="qwen2.5:3b"))

    # Step 4: Daily Target
    console.print("\n[bold]4. Daily Target[/bold]")
    tgt = click.prompt("Target % per day", type=float, default=15.0)
    if tgt > 50:
        console.print("[bold yellow]\u26a0 Target >50% is very aggressive. LLM will adapt.[/bold yellow]")
    _env_set("DAILY_TARGET_PCT", str(tgt))
    max_tr = click.prompt("Max trades per day", type=int, default=3)
    risk_tr = click.prompt("Risk % per trade", type=float, default=5.0)
    _env_set("MAX_TRADES", str(max_tr))
    _env_set("RISK_PER_TRADE", str(risk_tr))

    # Step 5: Telegram
    console.print("\n[bold]5. Telegram (optional)[/bold]")
    tg_bot = click.prompt("Bot Token", default="", show_default=False)
    if tg_bot.strip():
        _env_set("TELEGRAM_BOT_TOKEN", tg_bot)
        console.print("[green]Telegram configured.[/green]")
    else:
        console.print("[dim]Skipped.[/dim]")

    # Step 6: Identity
    from src.identity import AgentIdentity
    ident = AgentIdentity.generate()
    Path("data").mkdir(exist_ok=True)
    _env_set("AGENT_ID", ident.agent_id)

    console.print(f"\n[green]Identity generated: [bold cyan]{ident.agent_id}[/bold cyan][/green]")

    # Summary
    env = _env_load()
    console.print(Panel.fit(
        f"[bold green]Ready![/bold green]\n\n"
        f"Agent:  [cyan]{ident.agent_id}[/cyan]\n"
        f"Target: [yellow]{tgt}%[/yellow]/day | Max trades: {max_tr} | Risk: {risk_tr}%\n"
        f"Model:  [cyan]{env.get('LLM_MODEL', '?')}[/cyan]\n"
        f"Provider: {prov}\n"
        f"Tier:   [bold]{_tier_label(bal)}[/bold]"
    ))

    # Ask to start now
    if click.confirm("\nStart Aethera now?", default=True):
        console.print("[cyan]Launching Aethera TUI...[/cyan]")
        # Build TUI if needed
        tui_dist = Path("tui/dist/cli.js")
        if not tui_dist.exists():
            console.print("[dim]Building TypeScript TUI...[/dim]")
            subprocess.run(
                ["npm", "install"],
                cwd=str(SCRIPT_DIR / "tui"),
                capture_output=True,
            )
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(SCRIPT_DIR / "tui"),
                capture_output=True,
            )

        # Start API server in background
        console.print("[dim]Starting API server...[/dim]")
        api_cmd = [sys.executable, str(SCRIPT_DIR / "api.py")]
        api_proc = subprocess.Popen(
            api_cmd,
            stdout=open("data/api.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(SCRIPT_DIR),
        )
        
        # Wait for API server to be ready
        api_ok = False
        for i in range(10):
            time.sleep(1)
            try:
                import urllib.request
                urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)
                api_ok = True
                break
            except Exception:
                pass
        if not api_ok:
            console.print("[red]API server failed to start within 10s[/red]")
            if Path("data/api.log").exists():
                log_lines = Path("data/api.log").read_text().split("\n")
                console.print("[dim]Last API log entries:[/dim]")
                for line in log_lines[-5:]:
                    if line.strip():
                        console.print(f"  [red]{line.strip()[:120]}[/red]")
            api_proc.kill()
            return

        # Launch TypeScript TUI
        tui_cmd = ["node", str(tui_dist)]
        try:
            subprocess.run(tui_cmd, cwd=str(SCRIPT_DIR))
        except KeyboardInterrupt:
            console.print("\n[yellow]TUI exited. Stopping API server...[/yellow]")
            api_proc.terminate()


# ---- model ----

@cli.command()
@click.option("--search", "-s", default=None, help="Filter models by name")
def model(search):
    """Browse and select LLM model from OpenRouter (live + pricing)."""
    console.print("[dim]Fetching models from OpenRouter...[/dim]")
    models = _fetch_openrouter_models()

    if not models:
        console.print("[red]Cannot reach OpenRouter. Check internet.[/red]")
        return

    if search:
        models = [m for m in models if search.lower() in m[0].lower() or search.lower() in m[1].lower()]
        if not models:
            console.print(f"[yellow]No models matching '{search}'[/yellow]")
            return

    console.print(_model_table(models))
    console.print("[dim]Type a number to select, type a model ID, or [bold]/search <term>[/bold] to filter[/dim]")

    while True:
        choice = click.prompt("Model", default="", show_default=False).strip()

        if not choice:
            continue

        if choice.startswith("/search ") or choice.startswith("/s "):
            term = choice.split(" ", 1)[1].strip()
            filtered = [m for m in models if term.lower() in m[0].lower() or term.lower() in m[1].lower()]
            if filtered:
                console.print(_model_table(filtered))
            else:
                console.print(f"[yellow]No match for '{term}'[/yellow]")
            continue

        # Number selection
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                selected = models[idx]
                break
            console.print("[yellow]Invalid number[/yellow]")
            continue
        except ValueError:
            pass

        # Name match
        for m in models:
            if m[0] == choice:
                selected = m
                break
        else:
            console.print(f"[yellow]Model '{choice}' not found. Type exact ID or use number.[/yellow]")
            continue
        break

    _env_set("LLM_MODEL", selected[0])
    if not _env_load().get("LLM_PROVIDER"):
        _env_set("LLM_PROVIDER", "openrouter")
    console.print(f"\n[green]Model: {selected[0]}[/green]")

    # Test connection
    console.print("[dim]Testing...[/dim]")
    try:
        from openai import OpenAI
        env = _env_load()
        ak = env.get("OPENROUTER_API_KEY") or env.get("LLM_API_KEY", "")
        if not ak:
            ak = click.prompt("OpenRouter API Key", hide_input=True, show_default=False)
            _env_set("OPENROUTER_API_KEY", ak)
        c = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=ak, timeout=10)
        r = c.chat.completions.create(model=selected[0], messages=[{"role": "user", "content": "OK"}], max_tokens=2)
        if r.choices:
            console.print("[green]Connection OK[/green]")
    except Exception as e:
        console.print(f"[yellow]Could not verify: {e}[/yellow]")


# ---- config show ----

@cli.group()
def config():
    """Manage configuration."""


@config.command()
def show():
    """Show current .env values (keys/secrets hidden)."""
    env = _env_load()
    if not env:
        console.print("[dim]No .env file found. Run [bold]aethera init[/bold].[/dim]")
        return

    table = Table(title="Current Configuration", border_style="dim")
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    sensitive_keys = {"BINANCE_API_KEY", "BINANCE_API_SECRET", "OPENROUTER_API_KEY",
                      "LLM_API_KEY", "TELEGRAM_BOT_TOKEN"}
    for k, v in sorted(env.items()):
        if k in sensitive_keys:
            v = v[:4] + "..." if len(v) > 4 else "***"
        table.add_row(k, v)

    console.print(table)


# ---- doctor ----

@cli.command()
def doctor():
    """System diagnostic check."""
    console.print("[bold]System Diagnostic[/bold]\n")

    # OS & Python
    py_ver = sys.version.split()[0]
    py_ok = tuple(map(int, py_ver.split("."))) >= (3, 10)
    console.print(f"  {'[green]\u2713[/green]' if py_ok else '[red]\u2717[/red]'} Python: {py_ver}")
    console.print(f"  {'[green]\u2713[/green]' if sys.platform else '[red]\u2717[/red]'} OS: {platform.system()} {platform.release()}")

    # pip
    import subprocess
    try:
        subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, check=True)
        console.print(f"  [green]\u2713[/green] pip")
    except Exception:
        console.print(f"  [red]\u2717[/red] pip")

    # git
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        console.print(f"  [green]\u2713[/green] git")
    except Exception:
        console.print(f"  [red]\u2717[/red] git")

    # disk space
    try:
        import shutil
        usage = shutil.disk_usage(".")
        gb_free = usage.free / (1024 ** 3)
        ok = "green" if gb_free > 1 else "yellow"
        console.print(f"  [{ok}]\u2713[/{ok}] Disk: {gb_free:.1f} GB free")
    except Exception:
        console.print(f"  [yellow]?[/yellow] Disk: cannot check")

    # internet
    ip = _public_ip()
    console.print(f"  {'[green]\u2713[/green]' if ip else '[red]\u2717[/red]'} Internet{' (' + ip + ')' if ip else ''}")

    # Python imports
    console.print("\n[bold]Dependencies:[/bold]")
    deps = ["click", "rich", "requests", "numpy", "pandas", "openai", "nacl", "ccxt"]
    for dep in deps:
        try:
            __import__(dep)
            console.print(f"  [green]\u2713[/green] {dep}")
        except ImportError:
            console.print(f"  [red]\u2717[/red] {dep}")


# =========================================================================
# TARGET COMMANDS
# =========================================================================

@cli.group()
def target():
    """Manage daily profit target settings."""


@target.command()
def show():
    """Show daily target config and progress."""
    _require_env()
    env = _env_load()
    bal = _balance()

    tgt_pct = float(env.get("DAILY_TARGET_PCT", 15))
    max_trades = int(env.get("MAX_TRADES", 3))
    risk_pct = float(env.get("RISK_PER_TRADE", 5))
    mode = env.get("TARGET_MODE", "adaptive")
    compound = env.get("COMPOUND", "off")
    max_dd = float(env.get("MAX_DD_PCT", 20))

    console.print(Panel.fit("[bold cyan]Daily Target[/bold cyan]"))
    console.print(f"  Mode:        [bold]{mode.upper()}[/bold]")
    console.print(f"  Target:      [yellow]{tgt_pct}%[/yellow]/day")
    if bal is not None:
        console.print(f"  Target USD:  [green]${bal * tgt_pct / 100:.2f}[/green]/day")
        console.print(f"  Balance:     [green]${bal:.2f}[/green]")
    console.print(f"  Max Trades:  {max_trades}/day")
    console.print(f"  Risk/Trade:  {risk_pct}%")
    console.print(f"  Max DD:      {max_dd}%")
    console.print(f"  Compounding: [bold]{'ON' if compound == 'on' else 'OFF'}[/bold]")

    # Daily state
    state_path = Path("data/daily_state.json")
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            console.print(f"\n[bold]Today's progress:[/bold]")
            console.print(f"  Trades:  {state.get('trades_taken', 0)}")
            pnl = state.get("current_pnl", 0)
            col = "green" if pnl >= 0 else "red"
            console.print(f"  PnL:     [{col}]${pnl:+.2f}[/{col}]")
        except Exception:
            pass


@target.command()
@click.argument("value", type=float)
def set(value):
    """Set daily target %. Max 75%, warns if >50%."""
    if value > 75:
        console.print("[red]Max target is 75%. Setting to 75%.[/red]")
        value = 75
    elif value > 50:
        console.print("[bold yellow]\u26a0 Target >50% is extremely aggressive.[/bold yellow]")
    _env_set("DAILY_TARGET_PCT", str(value))
    console.print(f"[green]Daily target set to {value}%[/green]")


@target.command()
@click.argument("mode", type=click.Choice(["adaptive", "fixed", "safe"]))
def mode(mode):
    """Set target mode: adaptive, fixed, or safe."""
    _env_set("TARGET_MODE", mode)
    labels = {"adaptive": "LLM adjusts target based on regime",
              "fixed": "Strict fixed percentage every day",
              "safe": "Conservative: stops after 70% target hit"}
    console.print(f"[green]Target mode: [bold]{mode}[/bold][/green]")
    console.print(f"[dim]{labels.get(mode, '')}[/dim]")


@target.group()
def risk():
    """Risk management parameters."""


@risk.command()
def show():
    """Show risk parameters."""
    env = _env_load()
    console.print(Panel.fit("[bold yellow]Risk Parameters[/bold yellow]"))
    console.print(f"  Risk per trade:    [yellow]{env.get('RISK_PER_TRADE', '5')}%[/yellow]")
    console.print(f"  Max trades/day:    {env.get('MAX_TRADES', '3')}")
    console.print(f"  Max daily drawdown: {env.get('MAX_DD_PCT', '20')}%")


@risk.command()
@click.argument("param")
@click.argument("value", type=float)
def set(param, value):
    """Set risk parameter. Usage: target risk set trade|max_trades|drawdown <N>"""
    if param == "trade":
        _env_set("RISK_PER_TRADE", str(value))
        console.print(f"[green]Risk per trade set to {value}%[/green]")
    elif param == "max_trades":
        _env_set("MAX_TRADES", str(int(value)))
        console.print(f"[green]Max trades/day set to {int(value)}[/green]")
    elif param == "drawdown":
        _env_set("MAX_DD_PCT", str(value))
        console.print(f"[green]Max daily drawdown set to {value}%[/green]")
    else:
        console.print("[red]Invalid param. Use: trade, max_trades, or drawdown[/red]")


@target.command()
@click.argument("state", type=click.Choice(["on", "off"]))
def compound(state):
    """Toggle compounding on/off."""
    _env_set("COMPOUND", state)
    console.print(f"[green]Compounding: [bold]{'ON' if state == 'on' else 'OFF'}[/bold][/green]")


@target.command()
def daily():
    """Show today's report."""
    _require_env()
    state_path = Path("data/daily_state.json")
    if not state_path.exists():
        console.print("[dim]No daily data yet. Start trading first.[/dim]")
        return

    env = _env_load()
    bal = _balance()
    state = json.loads(state_path.read_text())
    tgt_pct = float(env.get("DAILY_TARGET_PCT", 15))

    console.print(Panel.fit("[bold cyan]Today's Report[/bold cyan]"))
    console.print(f"  Target:     [yellow]{tgt_pct}%[/yellow]")
    if bal is not None:
        console.print(f"  Target USD: [green]${bal * tgt_pct / 100:.2f}[/green]")
    console.print(f"  Trades:     {state.get('trades_taken', 0)}/{env.get('MAX_TRADES', '3')}")
    pnl = state.get("current_pnl", 0)
    col = "green" if pnl >= 0 else "red"
    console.print(f"  PnL:        [{col}]${pnl:+.2f}[/{col}]")

    start_cap = state.get("capital_start", 0)
    if start_cap > 0:
        progress = pnl / (start_cap * tgt_pct / 100) * 100
        pcol = "green" if progress >= 0 else "red"
        console.print(f"  Progress:   [{pcol}]{progress:.1f}%[/{pcol}] of target")


@target.command()
@click.option("--days", type=int, default=7, help="Days of history")
def history(days):
    """Show last N days of target results."""
    _require_env()
    db_path = Path("data/screener.db")
    if not db_path.exists():
        console.print("[dim]No database. Run aethera start first.[/dim]")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT scan_date, total_signals, long_count, short_count,
                   wins, losses, total_pnl
            FROM daily_stats
            WHERE scan_date >= date('now', ?)
            ORDER BY scan_date DESC
        """, (f"-{days} days",))
        rows = c.fetchall()
        conn.close()

        if not rows:
            console.print("[dim]No history yet.[/dim]")
            return

        table = Table(title=f"Last {days} Days", border_style="dim")
        table.add_column("Date", style="cyan")
        table.add_column("Signals")
        table.add_column("L/S", justify="center")
        table.add_column("Win/Loss", justify="center")
        table.add_column("PnL", justify="right")

        for r in rows:
            pnl = r["total_pnl"] or 0
            pcol = "green" if pnl >= 0 else "red"
            table.add_row(
                r["scan_date"],
                str(r["total_signals"]),
                f"{r['long_count']}/{r['short_count']}",
                f"{r['wins']}/{r['losses']}",
                f"[{pcol}]{pnl:+.2f}%[/{pcol}]" if pnl else "-",
            )
        console.print(table)
    except Exception as e:
        console.print(f"[yellow]Error reading history: {e}[/yellow]")


# =========================================================================
# TRADING COMMANDS
# =========================================================================

@cli.command()
def start():
    """Start Aethera — API server + TypeScript TUI."""
    if not Path(".env").exists():
        console.print("[red]Not configured. Run: [bold]aethera init[/bold][/red]")
        return

    _env_apply()

    # Check if already running
    pid_file = Path("data/aethera.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            console.print(f"[yellow]Aethera already running (PID {pid})[/yellow]")
            console.print("[dim]Run: [bold]aethera stop[/bold] first[/dim]")
            return
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    # Build TUI if needed
    tui_dist = Path("tui/dist/cli.js")
    if not tui_dist.exists():
        console.print("[dim]Building TypeScript TUI...[/dim]")
        subprocess.run(
            ["npm", "install"],
            cwd=str(SCRIPT_DIR / "tui"),
            capture_output=True,
        )
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(SCRIPT_DIR / "tui"),
            capture_output=True,
        )
        if result.returncode != 0:
            console.print("[red]TUI build failed[/red]")
            return

    # Start API server in background
    console.print("[cyan]Starting Aethera API server...[/cyan]")
    api_cmd = [sys.executable, str(SCRIPT_DIR / "api.py")]

    api_proc = subprocess.Popen(
        api_cmd,
        stdout=open("data/api.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(SCRIPT_DIR),
    )

    # Write PID
    Path("data").mkdir(exist_ok=True)
    pid_file.write_text(str(api_proc.pid))

    # Wait for API to be ready
    console.print("[dim]Waiting for API server...[/dim]")
    for i in range(10):
        time.sleep(1)
        try:
            import urllib.request
            urllib.request.urlopen("http://127.0.0.1:8000/api/status", timeout=2)
            console.print("[green]API server ready[/green]")
            break
        except Exception:
            if i == 9:
                console.print("[red]API server failed to start within 10s[/red]")
                if Path("data/api.log").exists():
                    log_lines = Path("data/api.log").read_text().split("\n")
                    console.print("[dim]Last API log entries:[/dim]")
                    for line in log_lines[-5:]:
                        if line.strip():
                            console.print(f"  [red]{line.strip()[:120]}[/red]")
                api_proc.kill()
                pid_file.unlink(missing_ok=True)
                return

    # Launch TypeScript TUI
    console.print("[cyan]Launching Aethera TUI...[/cyan]")
    console.print("[dim]Press q to quit[/dim]\n")

    tui_cmd = ["node", str(tui_dist)]
    try:
        subprocess.run(tui_cmd, cwd=str(SCRIPT_DIR))
    except KeyboardInterrupt:
        pass
    finally:
        console.print("\n[yellow]TUI exited. Stopping API server...[/yellow]")
        try:
            api_proc.terminate()
            api_proc.wait(timeout=5)
        except Exception:
            api_proc.kill()
        pid_file.unlink(missing_ok=True)
        console.print("[green]Aethera stopped.[/green]")


@cli.command()
def stop():
    """Kill all Aethera processes."""
    pid_file = Path("data/aethera.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            pid_file.unlink()
            console.print(f"[yellow]Aethera stopped (PID {pid}).[/yellow]")
        except (ProcessLookupError, ValueError, FileNotFoundError):
            pid_file.unlink(missing_ok=True)
            console.print("[dim]Aethera process not running (stale PID removed).[/dim]")
    else:
        console.print("[dim]No PID file found. Aethera may not be running.[/dim]")


@cli.command()
@click.option("--port", type=int, default=8000, help="Web server port")
def dashboard(port):
    """Launch web dashboard with engine (http://localhost:PORT)."""
    console.print(f"[cyan]Starting dashboard on http://localhost:{port}[/cyan]")
    console.print("[dim]Open browser → http://localhost:{}[/dim]".format(port))
    console.print("[dim]Includes: signals, account, trade, hivemind, data[/dim]\n")
    try:
        import uvicorn
        uvicorn.run("api:app", host="0.0.0.0", port=port, log_level="warning")
    except ImportError:
        console.print("[red]uvicorn not installed. Run: pip install uvicorn[/red]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


@cli.command()
def restart():
    """Stop and restart Aethera."""
    pid_file = Path("data/aethera.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            pid_file.unlink()
            time.sleep(1)
        except (ProcessLookupError, ValueError, FileNotFoundError):
            pid_file.unlink(missing_ok=True)
    console.print("[dim]Restarting...[/dim]")
    subprocess.run(
        [sys.executable, str(__file__), "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    console.print("[green]Aethera restarted in background.[/green]")


# =========================================================================
# MONITORING COMMANDS
# =========================================================================

@cli.command()
def status():
    """Show account overview."""
    env = _env_load()
    ip = _public_ip()
    bal = _balance()

    console.print(Panel.fit("[bold cyan]Aethera v1.5[/bold cyan]"))
    console.print(f"  IP:        [green]{ip or 'Unknown'}[/green]")
    if bal is not None:
        tgt = float(env.get("DAILY_TARGET_PCT", 15))
        tier = _tier_label(bal)
        console.print(f"  Balance:   [green]${bal:.2f}[/green]")
        console.print(f"  Target:    [yellow]{tgt}%[/yellow] = ${bal * tgt / 100:.2f}/day")
        console.print(f"  Tier:      [bold]{tier}[/bold]")
    else:
        console.print(f"  Balance:   [yellow]Cannot fetch (check API keys)[/yellow]")
    console.print(f"  Model:     [cyan]{env.get('LLM_MODEL', '?')}[/cyan]")
    console.print(f"  Provider:  {env.get('LLM_PROVIDER', '?')}")

    # Swarm status
    swarm_url = env.get("HIVEMIND_URL", "")
    if swarm_url:
        console.print(f"  Swarm:     [green]Connected[/green] ({swarm_url})")
    else:
        console.print(f"  Swarm:     [dim]Not configured[/dim]")

    console.print(f"\n[dim]aethera start  = begin trading[/dim]")
    console.print(f"[dim]aethera --help  = all commands[/dim]")


@cli.command()
def positions():
    """Show open positions from Binance."""
    env = _env_load()
    api_key = env.get("BINANCE_API_KEY", "")
    api_secret = env.get("BINANCE_API_SECRET", "")
    if not api_key:
        console.print("[red]No API keys configured.[/red]")
        return

    try:
        import ccxt
        ex = ccxt.binance({"apiKey": api_key, "secret": api_secret,
                           "options": {"defaultType": "future"}, "enableRateLimit": True})
        pos = ex.fetch_positions()
        active = [p for p in pos if float(p.get("contracts", 0)) > 0]
        if not active:
            console.print("[dim]No open positions.[/dim]")
            return

        table = Table(title="Open Positions", border_style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Side")
        table.add_column("Size", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("Mark", justify="right")
        table.add_column("PnL", justify="right")

        for p in active:
            sym = p["symbol"].replace(":USDT", "").replace("/USDT", "")
            side = p.get("side", "?")
            side_col = "green" if "long" in side.lower() else "red" if "short" in side.lower() else "dim"
            pnl = float(p.get("unrealizedPnl", 0))
            pnl_col = "green" if pnl >= 0 else "red"
            table.add_row(
                sym,
                f"[{side_col}]{side.upper()}[/{side_col}]",
                p.get("contracts", "?"),
                str(p.get("entryPrice", "?")),
                str(p.get("markPrice", "?")),
                f"[{pnl_col}]{pnl:+.2f}[/{pnl_col}]",
            )
        console.print(table)
    except Exception as e:
        console.print(f"[yellow]Could not fetch positions: {e}[/yellow]")


@cli.command()
def signals():
    """Show latest signals from database."""
    db_path = Path("data/screener.db")
    if not db_path.exists():
        console.print("[dim]No signals yet. Run [bold]aethera start[/bold].[/dim]")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT symbol, signal, confidence, composite_score, regime, timestamp
            FROM signals ORDER BY id DESC LIMIT 20
        """).fetchall()
        conn.close()

        if not rows:
            console.print("[dim]No signals yet.[/dim]")
            return

        table = Table(title="Latest Signals", border_style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Signal")
        table.add_column("Conf", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Regime")
        table.add_column("Time", style="dim")

        for r in rows:
            sig = r["signal"]
            col = "green" if sig == "LONG" else "red" if sig == "SHORT" else "dim"
            table.add_row(
                r["symbol"],
                f"[{col}]{sig}[/{col}]",
                str(r["confidence"]),
                f"{r['composite_score']:.1f}",
                r["regime"] or "?",
                str(r["timestamp"])[:16],
            )
        console.print(table)
    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


# =========================================================================
# ANALYSIS COMMANDS
# =========================================================================

@cli.command()
@click.option("--days", type=int, default=30, help="Days of data to analyze")
def stats(days):
    """Performance summary: PnL, WR, PF, Sharpe, best/worst pair."""
    db_path = Path("data/screener.db")
    if not db_path.exists():
        console.print("[dim]No data yet.[/dim]")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Overall stats
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
                   AVG(CASE WHEN result IN ('WIN','LOSS') THEN pnl_pct END) as avg_pnl,
                   AVG(CASE WHEN result IN ('WIN','LOSS') AND pnl_pct>0 THEN pnl_pct END) as avg_win,
                   AVG(CASE WHEN result IN ('WIN','LOSS') AND pnl_pct<0 THEN pnl_pct END) as avg_loss
            FROM signals WHERE result IN ('WIN','LOSS')
            AND timestamp >= date('now', ?)
        """, (f"-{days} days",)).fetchone()

        # Best/worst pairs
        pairs = conn.execute("""
            SELECT symbol, result, pnl_pct
            FROM signals WHERE result IN ('WIN','LOSS')
            AND timestamp >= date('now', ?)
        """, (f"-{days} days",)).fetchall()
        conn.close()

        if not row or row["total"] == 0:
            console.print("[dim]No closed trades yet.[/dim]")
            return

        total = row["total"]
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        wr = wins / total * 100 if total > 0 else 0

        # Per-pair
        pair_stats = {}
        for p in pairs:
            sym = p["symbol"]
            pnl = p["pnl_pct"] or 0
            pair_stats.setdefault(sym, {"wins": 0, "trades": 0, "pnl": 0})
            pair_stats[sym]["trades"] += 1
            pair_stats[sym]["pnl"] += pnl
            if p["result"] == "WIN":
                pair_stats[sym]["wins"] += 1

        sorted_pairs = sorted(pair_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
        best = sorted_pairs[0] if sorted_pairs else ("-", {})
        worst = sorted_pairs[-1] if sorted_pairs else ("-", {})

        console.print(Panel.fit(f"[bold cyan]Performance — Last {days} Days[/bold cyan]"))
        console.print(f"  Total trades:  {total}")
        console.print(f"  Win rate:      [bold]{wr:.1f}%[/bold] ({wins}W/{losses}L)")

        avg_w = row["avg_win"] or 0
        avg_l = abs(row["avg_loss"] or 0)
        pf = f"{avg_w / avg_l:.2f}" if avg_l > 0 else "\u221e"
        console.print(f"  Profit factor: [bold]{pf}[/bold]")
        console.print(f"  Avg PnL:       [{'green' if (row['avg_pnl'] or 0) >= 0 else 'red'}]{(row['avg_pnl'] or 0):+.2f}%[/{'green' if (row['avg_pnl'] or 0) >= 0 else 'red'}]")
        console.print(f"  Best pair:     [green]{best[0]}[/green] ({best[1]['pnl']:+.1f}%, {best[1]['wins']}/{best[1]['trades']})")
        console.print(f"  Worst pair:    [red]{worst[0]}[/red] ({worst[1]['pnl']:+.1f}%, {worst[1]['wins']}/{worst[1]['trades']})")

    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


@cli.command()
@click.option("--capital", type=float, default=25, help="Initial capital")
@click.option("--days", type=int, default=90, help="Lookback days")
def backtest(capital, days):
    """Run backtest on historical signals."""
    from src.backtest_engine import BacktestEngine

    engine = BacktestEngine("data/screener.db")
    # Fix missing PnL first
    fixed = engine.recalc_and_fix_pnl()
    if fixed > 0:
        console.print(f"[dim]Fixed {fixed} signals with missing PnL.[/dim]")

    result = engine.run(initial_capital=capital, risk_pct=0.05)

    console.print(Panel.fit("[bold cyan]Backtest Results[/bold cyan]"))
    console.print(f"  Capital:      ${result.initial_capital:,.2f}")
    console.print(f"  Final:        [{'green' if result.final_capital > result.initial_capital else 'red'}]${result.final_capital:,.2f}[/{'green' if result.final_capital > result.initial_capital else 'red'}]")
    console.print(f"  Return:       [{'green' if result.total_return_pct >= 0 else 'red'}]{result.total_return_pct:+.2f}%[/{'green' if result.total_return_pct >= 0 else 'red'}]")
    console.print(f"  Trades:       {result.trades} ({result.wins}W / {result.losses}L)")
    console.print(f"  Win Rate:     {result.win_rate:.1f}%")
    console.print(f"  Profit Factor: {result.profit_factor}")
    console.print(f"  Sharpe:       {result.sharpe_ratio}")
    console.print(f"  EV per R:     {result.expected_value_r:.3f}")
    console.print(f"  Max DD:       [red]{result.max_drawdown_pct:.2f}%[/red]")
    console.print(f"  Avg R:R:      {result.avg_rr_ratio:.2f}")

    if result.regime_breakdown:
        console.print("\n[bold]By Regime+Signal:[/bold]")
        for combo, data in sorted(result.regime_breakdown.items(), key=lambda x: x[1]["pnl_total"], reverse=True)[:10]:
            pcol = "green" if data["pnl_total"] >= 0 else "red"
            console.print(f"  {combo:<20} WR {data['wr']:>5.1f}%  n={data['n']:>3}  PnL [{pcol}]{data['pnl_total']:+.1f}[/{pcol}]")


@cli.command()
def lessons():
    """List recent trading lessons."""
    try:
        from src.lessons import get_recent_lessons
        recent = get_recent_lessons(30)
        if not recent:
            console.print("[dim]No lessons yet. Close some trades first.[/dim]")
            return

        console.print(f"[bold]{len(recent)} Lessons[/bold]\n")

        wins = sum(1 for l in recent if l.get("result") == "WIN")
        losses = sum(1 for l in recent if l.get("result") == "LOSS")
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        console.print(f"  [{green if wr >= 50 else 'red'}]WR: {wr:.0f}%[/{green if wr >= 50 else 'red'}] ({wins}W/{losses}L)\n")

        for i, l in enumerate(recent[-15:]):
            sym = l.get("symbol", "")
            res = l.get("result", "")
            rcol = "green" if res == "WIN" else "red" if res == "LOSS" else "dim"
            console.print(f"  [{rcol}]{res:<5}[/{rcol}] [cyan]{sym:<10}[/cyan] {l.get('lesson', '')[:120]}")
    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


@cli.group()
def memory():
    """Agent memory management."""


@memory.command()
def show():
    """Show MEMORY.md content."""
    mem_path = Path("data/MEMORY.md")
    if not mem_path.exists():
        console.print("[dim]No memory yet. Memory builds as the agent trades.[/dim]")
        return

    content = mem_path.read_text()
    console.print(Panel(content, title="Agent Memory", border_style="dim"))


@cli.command()
@click.option("--search", "-s", default=None, help="Search term")
@click.option("--limit", "-n", default=20, help="Max entries")
def log(search, limit):
    """Search decision log."""
    log_path = Path("data/decision_log.json")
    if not log_path.exists():
        console.print("[dim]No decision log yet.[/dim]")
        return

    try:
        entries = json.loads(log_path.read_text())
        if search:
            entries = [e for e in entries if search.lower() in json.dumps(e).lower()]

        entries = entries[-limit:]

        if not entries:
            console.print(f"[dim]No entries matching '{search}'[/dim]")
            return

        table = Table(title="Decision Log", border_style="dim")
        table.add_column("Time", style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Decision")
        table.add_column("Conf", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Reasons", max_width=40)

        for e in entries:
            dec = e.get("decision", "?")
            dcol = "green" if dec == "LONG" else "red" if dec == "SHORT" else "dim"
            blocked = " [red]BLK[/red]" if e.get("blocked") else ""
            reasons = ", ".join(e.get("reasons", [])[:3])
            table.add_row(
                e.get("timestamp", "")[:16],
                e.get("symbol", "?"),
                f"[{dcol}]{dec}[/{dcol}]{blocked}",
                str(e.get("confidence", 0)),
                f"{e.get('composite_score', 0):.1f}",
                reasons[:60],
            )
        console.print(table)
    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


# =========================================================================
# SWARM COMMANDS
# =========================================================================

@cli.group()
def swarm():
    """Swarm intelligence management."""


@swarm.command()
def status():
    """Show swarm connection status."""
    env = _env_load()
    url = env.get("HIVEMIND_URL", "")

    if not url:
        console.print("[dim]Swarm not configured. Set with: [bold]aethera swarm connect <url>[/bold][/dim]")
        return

    console.print(Panel.fit("[bold cyan]Swarm Status[/bold cyan]"))
    console.print(f"  Server URL: [green]{url}[/green]")
    console.print(f"  Agent ID:   [cyan]{env.get('AGENT_ID', '?')}[/cyan]")

    try:
        import requests
        r = requests.get(f"{url.rstrip('/')}/health", timeout=5)
        if r.status_code == 200:
            data = r.json()
            console.print(f"  State:      [green]Online[/green]")
            console.print(f"  Agents:     {data.get('agents', 0)}")
            console.print(f"  Lessons:    {data.get('lessons', 0)}")
        else:
            console.print(f"  State:      [yellow]Unreachable ({r.status_code})[/yellow]")
    except Exception as e:
        console.print(f"  State:      [red]Offline — {e}[/red]")


@swarm.command()
@click.argument("url")
def connect(url):
    """Set swarm server URL."""
    if url.startswith("http://"):
        console.print("[bold red]Insecure HTTP — your swarm data will be sent in plaintext.[/bold red]")
        console.print("[yellow]Use an HTTPS URL to encrypt data in transit.[/yellow]")
        if not click.confirm("Save this insecure URL anyway?", default=False):
            return
    elif not url.startswith("https://"):
        console.print("[red]Invalid URL. Must start with https://[/red]")
        return

    _env_set("HIVEMIND_URL", url)
    console.print(f"[green]Swarm server set to: {url}[/green]")

    # Try to register
    try:
        from src.identity import AgentIdentity
        from src.hivemind_client import HiveMindClient
        client = HiveMindClient(server_url=url)
        if client.register():
            console.print(f"[green]Registered with swarm.[/green]")
        else:
            console.print(f"[yellow]Registration failed (server may be offline).[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Registration error: {e}[/yellow]")


@swarm.command()
def push():
    """Push local lessons to swarm server."""
    env = _env_load()
    url = env.get("HIVEMIND_URL", "")
    if not url:
        console.print("[red]No swarm configured. Use: aethera swarm connect <url>[/red]")
        return

    try:
        from src.lessons import get_recent_lessons
        from src.hivemind_client import HiveMindClient

        lessons = get_recent_lessons(200)
        if not lessons:
            console.print("[dim]No lessons to push.[/dim]")
            return

        client = HiveMindClient(server_url=url)
        pushed = 0
        for l in lessons:
            rule = l.get("lesson", "")[:400]
            if client.push_lesson(
                rule=rule,
                tags=[],
                regime=l.get("regime", ""),
                signal=l.get("signal", ""),
                result=l.get("result", ""),
                pnl_pct=0,
                confidence=0,
            ):
                pushed += 1

        console.print(f"[green]Pushed {pushed}/{len(lessons)} lessons.[/green]")
    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


@swarm.command()
@click.option("--regime", default=None, help="Filter by regime")
@click.option("--signal", default=None, help="Filter by signal")
@click.option("--limit", default=20, help="Max lessons")
def pull(regime, signal, limit):
    """Pull crowd lessons from swarm server."""
    env = _env_load()
    url = env.get("HIVEMIND_URL", "")
    if not url:
        console.print("[red]No swarm configured. Use: aethera swarm connect <url>[/red]")
        return

    try:
        from src.hivemind_client import HiveMindClient
        client = HiveMindClient(server_url=url)
        lessons = client.pull_lessons(regime=regime, signal=signal, limit=limit)

        if not lessons:
            console.print("[dim]No crowd lessons available.[/dim]")
            return

        console.print(f"[green]Pulled {len(lessons)} crowd lessons:[/green]\n")
        for i, l in enumerate(lessons[:20]):
            rule = l.get("rule", "")[:120]
            score = l.get("score", 1)
            regime = l.get("regime", "")
            signal = l.get("signal", "")
            console.print(f"  [{score}] [cyan]{regime}/{signal}[/cyan] {rule}")
    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


# =========================================================================
# SECURITY COMMANDS
# =========================================================================

@cli.group()
def identity():
    """Agent identity management."""


@identity.command()
def show():
    """Show agent_id and public key."""
    try:
        from src.identity import AgentIdentity
        ident = AgentIdentity.load()
        if ident is None:
            console.print("[yellow]No identity found. Run [bold]aethera init[/bold].[/yellow]")
            return

        console.print(Panel.fit("[bold cyan]Agent Identity[/bold cyan]"))
        console.print(f"  Agent ID:   [bold cyan]{ident.agent_id}[/bold cyan]")
        console.print(f"  Public Key: [dim]{ident.public_key_hex}[/dim]")

        # ── Swarm connection status ──
        env = _env_load()
        swarm_url = env.get("HIVEMIND_URL", "")
        if swarm_url:
            console.print(f"  Swarm URL:  [green]{swarm_url}[/green]")
            try:
                import requests
                r = requests.get(f"{swarm_url.rstrip('/')}/health", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    console.print(f"  Swarm:      [green]Connected[/green]"
                                 f" ({data.get('agents', 0)} agents, {data.get('lessons', 0)} lessons)")
                else:
                    console.print(f"  Swarm:      [yellow]Unreachable ({r.status_code})[/yellow]")
            except Exception as e:
                console.print(f"  Swarm:      [red]Offline — {e}[/red]")
        else:
            console.print(f"  Swarm:      [dim]Not connected[/dim]")
    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


@cli.group()
def audit():
    """Audit chain management."""


@audit.command()
def verify():
    """Verify audit chain integrity."""
    try:
        from src.audit_chain import get_audit_chain
        chain = get_audit_chain()
        ok = chain.verify()
        entries = chain.get_entries(100)

        if ok:
            console.print(f"[green]Chain integrity: VALID[/green] ({len(entries)} entries)")
        else:
            console.print(f"[red]Chain integrity: TAMPERED[/red] ({len(entries)} entries)")
            console.print(f"[yellow]Chain integrity compromised. Review data/audit_chain.json[/yellow]")

        # ── Show last 5 entries ──
        recent = chain.get_entries(5)
        if recent:
            console.print("\n[bold]Last 5 audit entries:[/bold]")
            for e in recent:
                t = e.get("entry", {})
                etype = t.get("type", "?")
                ts = e.get("timestamp", "")[:19]
                idx = e.get("index", "?")

                if etype == "strategy_change":
                    console.print(f"  [{idx}] [cyan]{etype}[/cyan] "
                                 f"direction={t.get('direction','')} "
                                 f"pairs={len(t.get('pairs',[]))} "
                                 f"conf={t.get('confidence_threshold',0)}%")
                elif etype == "trade_outcome":
                    col = "green" if t.get("result") == "WIN" else "red" if t.get("result") == "LOSS" else "dim"
                    console.print(f"  [{idx}] [cyan]{etype}[/cyan] "
                                 f"[{col}]{t.get('result','?')}[/{col}] "
                                 f"{t.get('symbol','')} {t.get('signal','')} "
                                 f"{t.get('pnl_pct',0):+.2f}%")
                elif etype == "cooldown_block":
                    console.print(f"  [{idx}] [yellow]{etype}[/yellow] "
                                 f"{t.get('symbol','')} "
                                 f"until {t.get('until','?')[:16]}")
                elif etype == "lesson_pushed":
                    console.print(f"  [{idx}] [dim]{etype}[/dim] "
                                 f"{t.get('regime','')}/{t.get('signal','')} "
                                 f"{t.get('rule','')[:80]}")
                elif etype == "skill_created":
                    console.print(f"  [{idx}] [dim]{etype}[/dim] "
                                 f"{t.get('name','')}: {t.get('description','')[:80]}")
                else:
                    console.print(f"  [{idx}] [{dim}]{etype}[/{dim}] {ts}")
        else:
            console.print("[dim]No audit entries yet.[/dim]")
    except Exception as e:
        console.print(f"[yellow]Error: {e}[/yellow]")


# =========================================================================
# MAINTENANCE COMMANDS
# =========================================================================

@cli.command()
def update():
    """Git pull and reinstall."""
    console.print("[dim]Pulling latest code...[/dim]")
    result = subprocess.run(["git", "pull"], capture_output=True, text=True)
    if result.returncode != 0:
        console.print("[yellow]git pull failed. Continuing anyway...[/yellow]")
    console.print("[dim]Installing...[/dim]")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    console.print("[green]Updated.[/green]")


@cli.command()
def version():
    """Show version info."""
    console.print(f"[bold cyan]Aethera v1.5.0[/bold cyan]")
    console.print(f"  Python: [dim]{sys.version.split()[0]}[/dim]")
    console.print(f"  Path:   [dim]{SCRIPT_DIR}[/dim]")


@cli.command()
@click.option("--rebuild-tui", is_flag=True, help="Force rebuild TypeScript TUI")
def update(rebuild_tui):
    """Update Aethera to latest version."""
    console.print("[bold cyan]Checking for updates...[/bold cyan]")

    # Check if git repo
    if not (SCRIPT_DIR / ".git").exists():
        console.print("[red]Not a git installation. Re-run install script.[/red]")
        return

    # Fetch latest
    console.print("[dim]Fetching latest from GitHub...[/dim]")
    result = subprocess.run(
        ["git", "fetch", "origin", "main"],
        cwd=str(SCRIPT_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Fetch failed: {result.stderr.strip()}[/red]")
        return

    # Check if updates available
    result = subprocess.run(
        ["git", "rev-list", "HEAD..origin/main", "--count"],
        cwd=str(SCRIPT_DIR),
        capture_output=True,
        text=True,
    )
    commits = int(result.stdout.strip()) if result.stdout.strip() else 0

    if commits == 0:
        console.print("[green]Already up to date.[/green]")
        return

    console.print(f"[yellow]{commits} update(s) available[/yellow]")

    # Backup data
    console.print("[dim]Backing up data/ and .env...[/dim]")
    backup_dir = SCRIPT_DIR / "data" / "backup_pre_update"
    backup_dir.mkdir(exist_ok=True)
    if Path("data/screener.db").exists():
        import shutil
        shutil.copy2("data/screener.db", backup_dir / "screener.db")
    if Path(".env").exists():
        shutil.copy2(".env", backup_dir / ".env")
    console.print("[green]Backup saved[/green]")

    # Pull updates
    console.print("[dim]Pulling updates...[/dim]")
    result = subprocess.run(
        ["git", "pull", "--ff-only", "origin", "main"],
        cwd=str(SCRIPT_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Pull failed: {result.stderr.strip()}[/red]")
        console.print("[dim]Try: git stash && git pull[/dim]")
        return

    console.print("[green]Code updated[/green]")

    # Update Python deps
    console.print("[dim]Updating Python dependencies...[/dim]")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "-r", "requirements.txt"],
        cwd=str(SCRIPT_DIR),
        capture_output=True,
    )
    console.print("[green]Dependencies updated[/green]")

    # Rebuild TUI
    if rebuild_tui or not (SCRIPT_DIR / "tui" / "dist" / "cli.js").exists():
        console.print("[dim]Rebuilding TypeScript TUI...[/dim]")
        subprocess.run(
            ["npm", "install"],
            cwd=str(SCRIPT_DIR / "tui"),
            capture_output=True,
        )
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(SCRIPT_DIR / "tui"),
            capture_output=True,
        )
        if result.returncode == 0:
            console.print("[green]TUI rebuilt[/green]")
        else:
            console.print("[yellow]TUI build failed — run 'cd tui && npm run build' manually[/yellow]")
    else:
        console.print("[dim]TUI up to date (skip with --rebuild-tui to force)[/dim]")

    console.print(f"\n[bold green]Update complete![/bold green]")
    console.print("[dim]Run: [bold]aethera start[/bold][/dim]")


@cli.command()
def cleanup():
    """Clean old logs and session files."""
    cleaned = 0
    # Clean decision log (keep last 500)
    log_path = Path("data/decision_log.json")
    if log_path.exists():
        try:
            entries = json.loads(log_path.read_text())
            if len(entries) > 500:
                json.dump(entries[-500:], open(str(log_path), "w"), indent=2)
                cleaned += 1
        except Exception:
            pass

    # Clean old sessions in data/
    for f in Path("data").glob("*.log"):
        try:
            f.unlink()
            cleaned += 1
        except Exception:
            pass

    console.print(f"[green]Cleanup done. Removed {cleaned} stale files.[/green]")


@cli.command()
@click.option("--format", "-f", "fmt", type=click.Choice(["csv", "json"]), default="csv", help="Export format")
def export(fmt):
    """Export trade data."""
    db_path = Path("data/screener.db")
    if not db_path.exists():
        console.print("[dim]No data to export.[/dim]")
        return

    try:
        import csv as csv_mod

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM signals WHERE result IN ('WIN','LOSS')
            ORDER BY id DESC LIMIT 5000
        """).fetchall()
        conn.close()

        if not rows:
            console.print("[dim]No closed trades to export.[/dim]")
            return

        if fmt == "csv":
            out_path = "data/trades_export.csv"
            with open(out_path, "w", newline="") as f:
                w = csv_mod.writer(f)
                w.writerow(rows[0].keys())
                for r in rows:
                    w.writerow(r)
            console.print(f"[green]Exported {len(rows)} trades to {out_path}[/green]")
        else:
            out_path = "data/trades_export.json"
            data = [dict(r) for r in rows]
            with open(out_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        console.print(f"[green]Exported to {out_path}[/green]")
    except Exception as e:
        console.print(f"[yellow]Export error: {e}[/yellow]")


@cli.command()
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.option("--keep-data", is_flag=True, help="Preserve trading history")
def uninstall(yes, keep_data):
    """Remove Aethera from this system."""
    import shutil

    if not yes:
        console.print("[yellow]This will remove Aethera from your system.[/yellow]")
        if not keep_data:
            console.print("[red]ALL trading history, lessons, and skills will be deleted.[/red]")
        if not click.confirm("Continue?"):
            return

    project_dir = Path(__file__).parent
    symlink = Path.home() / ".local" / "bin" / "aethera"

    # Remove symlink
    if symlink.exists():
        symlink.unlink()
        console.print("  ✓ Removed aethera command")

    # Remove or keep data
    data_dir = project_dir / "data"
    if keep_data and data_dir.exists():
        backup = Path.home() / "aethera-data-backup"
        shutil.copytree(data_dir, backup, dirs_exist_ok=True)
        console.print(f"  ✓ Data backed up to {backup}")

    # Remove .env
    env_file = project_dir / ".env"
    if env_file.exists():
        env_file.unlink()
        console.print("  ✓ Removed .env (API keys)")

    # Remove venv
    venv_dir = project_dir / "venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
        console.print("  ✓ Removed virtual environment")

    console.print(f"\n[green]Aethera uninstalled.[/green]")
    console.print(f"[dim]Project directory remains at: {project_dir}[/dim]")
    if not yes:
        console.print(f"[dim]Delete manually if desired: rm -rf {project_dir}[/dim]")


# =========================================================================
# READINESS CHECK
# =========================================================================

@cli.command()
def ready():
    """Check if system is ready to start autonomous trading."""
    console.print("[bold]Checking system readiness...[/bold]\n")

    required = []
    optional = []

    # Python version
    py_ok = tuple(map(int, sys.version.split(".")[:2])) >= (3, 10)
    required.append(("Python 3.10+", py_ok, sys.version.split()[0]))

    # Dependencies
    deps_ok = True
    for dep in ["click", "rich", "requests", "openai", "ccxt", "nacl"]:
        try:
            __import__(dep)
        except ImportError:
            deps_ok = False
    required.append(("Dependencies", deps_ok, "installed" if deps_ok else "missing"))

    # .env file
    env_exists = Path(".env").exists()
    required.append((".env file", env_exists, "found" if env_exists else "missing"))

    # Binance API
    env = _env_load()
    binance_ok = bool(env.get("BINANCE_API_KEY") and env.get("BINANCE_API_SECRET"))
    if binance_ok:
        bal = _balance()
        if bal is not None:
            required.append(("Binance API", True, f"${bal:.2f} (verified)"))
        else:
            required.append(("Binance API", False, "key set but connection failed"))
    else:
        required.append(("Binance API", False, "not configured"))

    # LLM
    llm_ok = bool(env.get("OPENROUTER_API_KEY") or env.get("LLM_API_KEY"))
    llm_model = env.get("LLM_MODEL", "?")
    if llm_ok:
        required.append(("LLM", True, f"{env.get('LLM_PROVIDER', 'openrouter')} — {llm_model}"))
    else:
        required.append(("LLM", False, "not configured"))

    # Agent identity
    ident_ok = bool(env.get("AGENT_ID"))
    required.append(("Agent Identity", ident_ok, env.get("AGENT_ID", "not generated")[:30]))

    # Optional: Telegram
    tg_ok = bool(env.get("TELEGRAM_BOT_TOKEN"))
    optional.append(("Telegram", tg_ok, "configured" if tg_ok else "not configured"))

    # Optional: Swarm
    swarm_ok = bool(env.get("HIVEMIND_URL"))
    optional.append(("Swarm", swarm_ok, env.get("HIVEMIND_URL", "not configured")[:40]))

    # Optional: user-config.json
    config_ok = Path("user-config.json").exists()
    optional.append(("user-config.json", config_ok, "found" if config_ok else "will use defaults"))

    # Print results
    all_ok = True
    for name, ok, value in required:
        icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
        if not ok:
            all_ok = False
        console.print(f"  {icon} {name:20s} [dim]{value}[/dim]")

    console.print("")
    for name, ok, value in optional:
        icon = "[green]✓[/green]" if ok else "[dim]✗[/dim]"
        console.print(f"  {icon} {name:20s} [dim]{value}[/dim]")

    console.print("")
    if all_ok:
        missing_optional = [n for n, ok, _ in optional if not ok]
        if missing_optional:
            console.print(f"[bold green]Status: READY TO START[/bold green] ({len(missing_optional)} optional items missing)")
        else:
            console.print("[bold green]Status: READY TO START[/bold green] (all configured)")
        console.print("\n[dim]Run: [bold]aethera start[/bold][/dim]")
    else:
        missing_required = [n for n, ok, _ in required if not ok]
        console.print(f"[bold red]Status: NOT READY[/bold red] ({len(missing_required)} required items missing)")
        console.print(f"\n[dim]Missing: {', '.join(missing_required)}[/dim]")
        console.print("[dim]Run: [bold]aethera init[/bold] to setup[/dim]")


# =========================================================================
# DAEMON COMMANDS
# =========================================================================

@cli.group()
def daemon():
    """Manage autonomous daemon process."""
    pass


@daemon.command()
@click.option("--trade", is_flag=True, help="Enable live trading")
def start(trade):
    """Start daemon in background."""
    if not Path(".env").exists():
        console.print("[red]Not configured. Run: [bold]aethera init[/bold][/red]")
        return

    pid_file = Path("data/aethera.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            console.print(f"[yellow]Daemon already running (PID {pid})[/yellow]")
            return
        except (ProcessLookupError, ValueError):
            pid_file.unlink(missing_ok=True)

    console.print("[cyan]Starting autonomous daemon...[/cyan]")

    # Start daemon as background process
    cmd = [sys.executable, str(SCRIPT_DIR / "src" / "daemon.py")]
    if trade:
        cmd.append("--trade")

    process = subprocess.Popen(
        cmd,
        stdout=open("data/daemon.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=str(SCRIPT_DIR),
    )

    time.sleep(2)

    if process.poll() is None:
        console.print(f"[green]Daemon started (PID {process.pid})[/green]")
        console.print("[dim]View logs: aethera daemon logs[/dim]")
        console.print("[dim]Stop: aethera daemon stop[/dim]")
    else:
        console.print("[red]Daemon failed to start. Check: aethera daemon logs[/red]")


@daemon.command()
def stop():
    """Stop daemon gracefully."""
    pid_file = Path("data/aethera.pid")
    if not pid_file.exists():
        console.print("[dim]Daemon not running (no PID file)[/dim]")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        console.print(f"[green]Daemon stopped (PID {pid})[/green]")
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        console.print("[dim]Daemon was not running (stale PID removed)[/dim]")
    except Exception as e:
        console.print(f"[red]Failed to stop daemon: {e}[/red]")


@daemon.command()
def status():
    """Check if daemon is running."""
    pid_file = Path("data/aethera.pid")
    if not pid_file.exists():
        console.print("[dim]Daemon not running[/dim]")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        console.print(f"[green]Daemon running (PID {pid})[/green]")

        # Show state
        state_file = Path("data/daemon_state.json")
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
                console.print(f"  Last screening: {state.get('last_screening', 'never')}")
                console.print(f"  Last management: {state.get('last_management', 'never')}")
                console.print(f"  Last reflection: {state.get('last_reflection', 'never')}")
                console.print(f"  Screening count: {state.get('screening_count', 0)}")
            except Exception:
                pass

        # Show debate stats
        try:
            _env_apply()
            from src.config_loader import get_config
            from src.engine_v2 import ScreeningEngineV2
            config = get_config()
            engine = ScreeningEngineV2(config, cache_dir="data")
            debate_stats = engine.get_debate_stats()
            console.print(f"\n[bold]Debate Pipeline:[/bold]")
            console.print(f"  Enabled: {'[green]Yes[/green]' if debate_stats['enabled'] else '[dim]No[/dim]'}")
            console.print(f"  Bull ready: {'[green]Yes[/green]' if debate_stats['bull_ready'] else '[red]No[/red]'}")
            console.print(f"  Bear ready: {'[green]Yes[/green]' if debate_stats['bear_ready'] else '[red]No[/red]'}")
            console.print(f"  Synthesizer ready: {'[green]Yes[/green]' if debate_stats['synth_ready'] else '[red]No[/red]'}")
            orch = debate_stats.get('orchestrator', {})
            if orch.get('total', 0) > 0:
                console.print(f"  Debates run: {orch['total']} (L:{orch.get('longs',0)} S:{orch.get('shorts',0)} W:{orch.get('waits',0)})")
                console.print(f"  Avg confidence: {orch.get('avg_confidence', 0):.1f}%")
            rg = debate_stats.get('risk_gate', {})
            if rg.get('total_overrides', 0) > 0:
                console.print(f"  Risk gate overrides: {rg['overrides_today']} today / {rg['total_overrides']} total")
        except Exception as e:
            console.print(f"[dim]  Debate stats unavailable: {e}[/dim]")

    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        console.print("[yellow]Daemon not running (stale PID removed)[/yellow]")


@daemon.command()
@click.option("--lines", "-n", default=50, help="Number of lines")
def logs(lines):
    """View daemon logs."""
    log_file = Path("data/daemon.log")
    if not log_file.exists():
        console.print("[dim]No daemon logs yet[/dim]")
        return

    try:
        content = log_file.read_text()
        last_lines = content.split("\n")[-lines:]
        for line in last_lines:
            if line.strip():
                console.print(line)
    except Exception as e:
        console.print(f"[red]Error reading logs: {e}[/red]")


@daemon.command()
def restart():
    """Restart daemon."""
    console.print("[dim]Restarting daemon...[/dim]")
    # Stop
    pid_file = Path("data/aethera.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
        except Exception:
            pass
        pid_file.unlink(missing_ok=True)

    # Start
    subprocess.run([sys.executable, str(__file__), "daemon", "start"])


# =========================================================================
# DEBATE COMMANDS
# =========================================================================

@cli.command()
@click.option("--lines", "-n", default=20, help="Number of recent signals")
def debate(lines):
    """Show debate pipeline stats and recent debate results."""
    _require_env()
    _env_apply()

    db_path = Path("data/screener.db")
    if not db_path.exists():
        console.print("[dim]No database yet. Run a scan first.[/dim]")
        return

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Signals with debate data
        rows = conn.execute("""
            SELECT symbol, signal, confidence, regime, timestamp,
                   debate_signal, debate_confidence, debate_overrode
            FROM signals
            WHERE debate_signal IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (lines,)).fetchall()

        # Debate stats from signals
        stats = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN debate_signal = 'LONG' THEN 1 ELSE 0 END) as longs,
                SUM(CASE WHEN debate_signal = 'SHORT' THEN 1 ELSE 0 END) as shorts,
                SUM(CASE WHEN debate_signal = 'WAIT' THEN 1 ELSE 0 END) as waits,
                AVG(debate_confidence) as avg_conf,
                SUM(CASE WHEN debate_overrode = 1 THEN 1 ELSE 0 END) as overrides
            FROM signals
            WHERE debate_signal IS NOT NULL
        """).fetchone()

        conn.close()

        if not rows:
            console.print("[dim]No debate data yet. Signals with confidence >= 50 will trigger debate.[/dim]")
            return

        # Stats panel
        total = stats["total"] or 0
        longs = stats["longs"] or 0
        shorts = stats["shorts"] or 0
        waits = stats["waits"] or 0
        avg_conf = stats["avg_conf"] or 0
        overrides = stats["overrides"] or 0

        console.print(Panel.fit(
            f"[bold cyan]Debate Pipeline Stats[/bold cyan]\n\n"
            f"  Total debates: {total}\n"
            f"  LONG: [green]{longs}[/green] | SHORT: [red]{shorts}[/red] | WAIT: [dim]{waits}[/dim]\n"
            f"  Avg confidence: {avg_conf:.1f}%\n"
            f"  Overrides: {overrides}",
            border_style="cyan",
        ))

        # Recent debates table
        table = Table(title=f"Recent Debates (last {len(rows)})", border_style="dim")
        table.add_column("Time", style="dim", width=16)
        table.add_column("Symbol", style="cyan", width=12)
        table.add_column("Quant", width=8)
        table.add_column("Debate", width=8)
        table.add_column("Conf", justify="right", width=6)
        table.add_column("Overrode", width=10)
        table.add_column("Regime", width=10)

        for r in rows:
            quant_sig = r.get("signal", "?")
            debate_sig = r.get("debate_signal", "?")
            qcol = "green" if quant_sig == "LONG" else "red" if quant_sig == "SHORT" else "dim"
            dcol = "green" if debate_sig == "LONG" else "red" if debate_sig == "SHORT" else "dim"
            overrode = "[yellow]YES[/yellow]" if r.get("debate_overrode") else "[dim]no[/dim]"

            table.add_row(
                str(r.get("timestamp", ""))[:16],
                r.get("symbol", "?"),
                f"[{qcol}]{quant_sig}[/{qcol}]",
                f"[{dcol}]{debate_sig}[/{dcol}]",
                f"{r.get('debate_confidence', 0):.0f}",
                overrode,
                r.get("regime", "?"),
            )

        console.print(table)

    except Exception as e:
        console.print(f"[yellow]Error reading debate data: {e}[/yellow]")


if __name__ == "__main__":
    cli()
