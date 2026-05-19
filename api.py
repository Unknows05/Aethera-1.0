"""
Coin Screener API — FastAPI server with background scheduler.
Uses shared ScreenerDB instance for all DB operations.
"""
import sys
import yaml
import logging
import asyncio
import os
import time
import json as _json_mod
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import ccxt
from fastapi import FastAPI, Form, Request, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from fastapi import Security, status
from apscheduler.schedulers.asyncio import AsyncIOScheduler

sys.path.insert(0, str(Path(__file__).parent))

# Load .env file — secure parsing with quote handling
env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        # Remove surrounding quotes (single or double)
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        # Skip if key looks like a command injection
        if any(c in k for c in (";", "|", "&", "$", "`", "(", ")")):
            logger.warning(f"[API] Skipping suspicious .env key: {k}")
            continue
        os.environ[k] = v

from src.engine_v2 import ScreeningEngineV2 as ScreeningEngine
from src.liquidation import liquidation_heatmap, update_liquidation_data
from src.telegram_bot import get_telegram_bot
from src.ml_engine import get_ml_engine
from src.auth import get_auth
from src.auto_trader import get_auto_trader
from src.key_manager import encrypt_key, decrypt_key
from src.decision_log import log_decision
from src.lessons import generate_lesson, add_lesson
from src.position_manager import PositionManager
from src.agent_screener import AgentScreener
from src.agent_manager import AgentManager
from src.coin_memory import record_trade
from src.hivemind_client import get_hivemind

# ── Globals ───────────────────────────────────────────────
engine: ScreeningEngine = None
scheduler: AsyncIOScheduler = None
config: dict = {}
telegram = None
_background_tasks: set = set()
_sentiment_cache = {"data": None, "last_fetched": 0, "prev_btc_dom": None}
_account_creds: dict = {}
_account_creds_file = Path("data/account_creds.json")
_shared_trader = None
_server_public_ip = "detecting..."

# ── WebSocket Manager ─────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        msg = _json_mod.dumps(data)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = ConnectionManager()

async def broadcast_event(event_type: str, data: dict):
    """Broadcast event to all connected TUI clients."""
    await ws_manager.broadcast({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now().isoformat(),
    })

# ── Logging ───────────────────────────────────────────────
Path("data").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler("data/api.log"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Lifecycle ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, scheduler, config, telegram

    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    engine = ScreeningEngine(config, cache_dir="data")
    logger.info("[API] Engine initialized with XGBoost ML v4")

    engine.risk_manager.reset_overfit_flag()

    telegram = get_telegram_bot(config.get("telegram", {}))

    scheduler = AsyncIOScheduler()
    interval = config.get("scan", {}).get("interval_minutes", 5)

    scheduler.add_job(auto_scan, "interval", minutes=interval, id="auto_scan",
                      replace_existing=True)
    scheduler.add_job(auto_check_outcomes, "interval", minutes=1, id="outcome_check",
                      replace_existing=True)
    scheduler.add_job(auto_fetch_historical, "interval", hours=1, id="historical_fetch",
                      replace_existing=True)
    scheduler.add_job(auto_fetch_premium, "interval", minutes=5, id="premium_fetch",
                      replace_existing=True)

    ml_day = config.get("ml", {}).get("retrain_day", "sat")
    ml_hour = config.get("ml", {}).get("retrain_hour", 3)
    ml_min = config.get("ml", {}).get("retrain_minute", 0)
    scheduler.add_job(auto_retrain_ml, "cron", day_of_week=ml_day, hour=ml_hour,
                      minute=ml_min, id="ml_retrain", replace_existing=True)
    scheduler.add_job(auto_daily_wr, "cron", hour=0, minute=5, id="daily_wr",
                      replace_existing=True)
    # Balance cache refresh every 30s if connected
    scheduler.add_job(_update_balance_cache_if_connected, "interval", seconds=30,
                      id="balance_cache", replace_existing=True)
    # Auto-retrain ML when enough new labeled data accumulates
    scheduler.add_job(auto_check_retrain, "interval", hours=1, id="retrain_check",
                      replace_existing=True)

    scheduler.start()

    next_run = datetime.now() + timedelta(minutes=interval)
    engine.set_next_scan(next_run.isoformat())
    logger.info(f"[API] Scheduler: every {interval}min | Next scan: {next_run:%H:%M:%S}")

    asyncio.create_task(engine.scan())
    asyncio.create_task(update_liquidation_data())
    asyncio.create_task(_detect_public_ip())
    # Immediate balance sync if connected
    asyncio.create_task(_update_balance_cache_if_connected())

    _load_account_creds()

    # Reconnect LLM with .env values loaded
    try:
        from src.llm_brain import get_llm_brain
        lb = get_llm_brain()
        lb.reconnect()
        logger.info(f"[API] LLM: {'ready' if lb.is_ready() else 'no key'}")
    except Exception as e:
        logger.debug(f"[API] LLM init: {e}")

    yield

    logger.info("[API] Shutting down...")
    if scheduler: scheduler.shutdown(wait=False)
    if telegram: telegram.close()
    if engine: engine.close()


# ── Account creds helpers ─────────────────────────────────

async def _detect_public_ip():
    """Detect server public IP once at startup, retry if needed."""
    global _server_public_ip
    import json as _j
    loop = asyncio.get_event_loop()
    try:
        def _fetch():
            from urllib.request import urlopen
            r = urlopen("https://api.ipify.org?format=json", timeout=8)
            return _j.loads(r.read().decode()).get("ip", "Unknown")
        ip = await loop.run_in_executor(None, _fetch)
        _server_public_ip = ip
        logger.info(f"[API] Public IP: {ip}")
    except Exception:
        _server_public_ip = "Unavailable"
        logger.warning("[API] Could not detect public IP")

def _load_account_creds():
    global _account_creds
    if _account_creds_file.exists():
        try:
            with open(_account_creds_file) as f:
                data = _json_mod.load(f)
            if data.get("api_key") and not data.get("api_key_enc"):
                data["api_key_enc"] = encrypt_key(data["api_key"])
                data["secret_enc"] = encrypt_key(data["secret"])
                del data["api_key"]
                del data["secret"]
                _save_account_creds()
            _account_creds = data
        except Exception:
            pass

def _save_account_creds():
    try:
        _account_creds_file.parent.mkdir(parents=True, exist_ok=True)
        with open(_account_creds_file, "w") as f:
            _json_mod.dump(_account_creds, f)
    except Exception as e:
        logger.error(f"[API] Failed to save creds: {e}")

def _get_api_key() -> str:
    if _account_creds.get("api_key_enc"):
        return decrypt_key(_account_creds["api_key_enc"])
    return _account_creds.get("api_key", "")

def _get_api_secret() -> str:
    if _account_creds.get("secret_enc"):
        return decrypt_key(_account_creds["secret_enc"])
    return _account_creds.get("secret", "")


# ── Scheduler jobs ────────────────────────────────────────

async def auto_scan():
    try:
        logger.info("[Scheduler] Auto-scan...")
        result = await engine.scan()
        if result.get("ok"):
            interval = config.get("scan", {}).get("interval_minutes", 5)
            next_run = datetime.now() + timedelta(minutes=interval)
            engine.set_next_scan(next_run.isoformat())
            logger.info(f"[Scheduler] Scan done: {result['summary']} | Next: {next_run:%H:%M:%S}")
        else:
            logger.error(f"[Scheduler] Scan failed: {result.get('error')}")
    except Exception as e:
        logger.error(f"[Scheduler] Auto-scan error: {e}")

async def auto_fetch_historical():
    try:
        symbols = engine.symbols
        loop = asyncio.get_event_loop()
        now_ms = int(datetime.now().timestamp() * 1000)
        db = engine.db
        for symbol in symbols[:10]:
            try:
                fund_data = await loop.run_in_executor(None, engine.api.get_funding_rate, symbol, 100)
                if fund_data:
                    for item in fund_data:
                        await db.save_funding_rate(
                            symbol=item.get('symbol', symbol),
                            funding_rate=float(item.get('fundingRate', 0)),
                            timestamp=int(item.get('fundingTime', now_ms)),
                            mark_price=float(item.get('markPrice', 0)) if 'markPrice' in item else None,
                        )
                oi_data = await loop.run_in_executor(None, engine.api.get_open_interest, symbol)
                if oi_data and isinstance(oi_data, dict):
                    await db.save_open_interest(
                        symbol=symbol,
                        open_interest=float(oi_data.get('openInterest', 0)),
                        timestamp=now_ms,
                        price=float(oi_data.get('price', 0)) if 'price' in oi_data else None,
                    )
            except Exception as e:
                logger.debug(f"Historical fetch error {symbol}: {e}")
        logger.info("[Scheduler] Historical data saved")
    except Exception as e:
        logger.error(f"[Scheduler] Historical fetch error: {e}")

async def auto_fetch_premium():
    try:
        symbols = engine.symbols[:5]
        loop = asyncio.get_event_loop()
        for symbol in symbols:
            try:
                data = await loop.run_in_executor(None, engine.api.get_mark_price, symbol)
                if isinstance(data, list) and data:
                    item = data[0] if isinstance(data[0], dict) else data
                    engine._premium_cache[symbol] = {
                        'funding_rate': float(item.get('lastFundingRate', 0)),
                        'mark_price': float(item.get('markPrice', 0)),
                        'index_price': float(item.get('indexPrice', 0)),
                        'updated_at': datetime.now().isoformat(),
                    }
            except Exception as e:
                logger.debug(f"Premium fetch error {symbol}: {e}")
    except Exception as e:
        logger.error(f"[Scheduler] Premium fetch error: {e}")

async def auto_check_outcomes():
    try:
        open_symbols = engine.db.get_open_signal_symbols()
        if not open_symbols: return
        loop = asyncio.get_event_loop()
        ticker_data = await loop.run_in_executor(None, engine.api.get_24h_ticker)
        prices = {t["symbol"]: float(t["lastPrice"]) for t in ticker_data if t["symbol"] in open_symbols}
        if prices:
            updated = await engine.db.check_outcomes(prices)
            if updated > 0:
                logger.info(f"[Scheduler] {updated} outcomes updated")
                conn = engine.db.conn
                c = conn.cursor()
                c.execute(
                    """SELECT s.id, s.result, s.symbol FROM signals s
                       INNER JOIN signal_features sf ON sf.signal_id = s.id
                       WHERE s.result IN ('WIN','LOSS') AND sf.outcome IS NULL
                       ORDER BY s.exit_timestamp DESC LIMIT ?""",
                    (updated * 2,),
                )
                for row in c.fetchall():
                    # CLOSE position on exchange + log to trade history
                    sym = row["symbol"]
                    result = row["result"]
                    try:
                        trader = _get_shared_trader()
                        if trader.executor and trader.executor.is_ready():
                            await loop.run_in_executor(None, trader.executor.close_position, sym)
                            logger.info(f"[Scheduler] Closed position on exchange: {sym}")
                    except Exception as e:
                        logger.warning(f"[Scheduler] Exchange close failed for {sym}: {e}")

                    # Append close event to trade log
                    trader = _get_shared_trader()
                    trader._trade_history.append({
                        "symbol": sym, "side": "CLOSE",
                        "entry": 0, "qty": 0, "sl": 0, "tp": 0,
                        "notional": 0, "fee": 0,
                        "result": result, "reason": "EXCHANGE_CLOSE",
                        "time": datetime.now().isoformat(),
                    })

                    await engine.db.update_signal_features_outcome(row["id"], row["result"])
                    engine.risk_manager.record_trade_result(row["result"] == "WIN")
                    # Record ML outcome for live WR tracking
                    try:
                        engine.ml_engine.record_outcome(0.5, row["result"] == "WIN")
                    except Exception: pass

                    sig_id = row["id"]
                    c2 = engine.db.conn.cursor()
                    c2.execute("SELECT symbol, signal, regime, confidence, pnl_pct, timestamp, exit_timestamp, exit_reason FROM signals WHERE id=?", (sig_id,))
                    sr = c2.fetchone()
                    if sr:
                        held = 0
                        try:
                            t0 = datetime.fromisoformat(sr["timestamp"])
                            t1 = datetime.fromisoformat(sr["exit_timestamp"]) if sr.get("exit_timestamp") else datetime.now().isoformat()
                            held = (datetime.fromisoformat(t1) - t0).total_seconds() / 3600
                        except Exception: pass
                        lesson = generate_lesson(sr["symbol"], sr["regime"], sr["signal"], sr["result"], sr["pnl_pct"] or 0, sr["confidence"] or 50, held)
                        add_lesson(lesson, sr["symbol"], sr["regime"], sr["signal"], sr["result"])
                        record_trade(sr["symbol"], sr["regime"], sr["signal"], sr["result"], sr["pnl_pct"] or 0, sr["confidence"] or 50, held)
                        try:
                            hive = get_hivemind()
                            hive.push_event(sr["symbol"], sr["regime"], sr["signal"], sr["result"], sr["pnl_pct"] or 0, held, sr.get("exit_reason", ""))
                        except Exception: pass

                # Sync balance after closing positions
                if _account_creds.get('connected'):
                    await _update_balance_cache()

            # Run AgentManager for open positions that need LLM decision
            try:
                ticker_data = await loop.run_in_executor(None, engine.api.get_24h_ticker)
                prices = {t["symbol"]: float(t["lastPrice"]) for t in ticker_data}
                from src.agent_manager import AgentManager
                mgr = AgentManager(engine)
                actions = mgr.run_cycle(prices)
                for action in actions:
                    if action.get("action") == "CLOSE":
                        sym = action["symbol"]
                        try:
                            trader = _get_shared_trader()
                            if trader.executor and trader.executor.is_ready():
                                await loop.run_in_executor(None, trader.executor.close_position, sym)
                                logger.info(f"[AgentManager] Closed position: {sym}")
                        except Exception as e:
                            logger.warning(f"[AgentManager] Close failed for {sym}: {e}")
            except Exception as e:
                logger.debug(f"[AgentManager] skip: {e}")
    except Exception as e:
        logger.error(f"[Scheduler] Outcome check error: {e}")

async def auto_retrain_ml():
    try:
        from src.backtest_engine import BacktestEngine
        be = BacktestEngine()
        fixed = be.recalc_and_fix_pnl()
        if fixed > 0:
            logger.info(f"[Scheduler] Fixed PnL for {fixed} signals")
        training_ready = be.get_training_ready_count()
        logger.info(f"[Scheduler] ML training samples: {training_ready}")
        lookback = config.get("ml", {}).get("lookback_days", 60)
        result = await engine.train_ml_model(lookback)
        if result.get("status") == "deployed":
            logger.info(f"[Scheduler] ML deployed: WR={result.get('wr_out_of_sample')}%")
            if telegram and telegram.is_ready():
                await telegram.send_ml_status(engine.ml_engine.get_status())
        else:
            logger.warning(f"[Scheduler] ML {result.get('status')}: {result.get('reason', '')}")
    except Exception as e:
        logger.error(f"[Scheduler] ML retrain error: {e}")

async def auto_daily_wr():
    try:
        wr_data = engine.db._calculate_daily_wr_simple()
        logger.info(f"[Scheduler] WR: daily={wr_data.get('daily',{}).get('wr')} 7d={wr_data.get('rolling_7d',{}).get('wr')}")
        if telegram and telegram.is_ready():
            await telegram.send_wr_update(wr_data)
    except Exception as e:
        logger.error(f"[Scheduler] Daily WR error: {e}")


async def _update_balance_cache_if_connected():
    if _account_creds.get('connected'):
        # Ensure executor exists for fast balance fetch
        trader = _get_shared_trader()
        if not trader.executor:
            trader.ensure_executor(_get_api_key(), _get_api_secret())
        await _update_balance_cache()


_last_retrain_samples = 0

async def auto_check_retrain():
    """Retrain ML if 100+ new labeled samples accumulated."""
    global _last_retrain_samples
    try:
        from src.backtest_engine import BacktestEngine
        be = BacktestEngine()
        current = be.get_training_ready_count()
        if _last_retrain_samples == 0:
            _last_retrain_samples = current
            return
        if current - _last_retrain_samples >= 100:
            logger.info(f"[Scheduler] Auto-retrain triggered: {current} samples (+{current - _last_retrain_samples})")
            result = await engine.train_ml_model(60)
            _last_retrain_samples = current
            if result.get("status") == "deployed":
                logger.info(f"[Scheduler] ML deployed: WR={result.get('wr_out_of_sample')}%")
            else:
                logger.info(f"[Scheduler] ML {result.get('status')}: {result.get('reason', '')}")
    except Exception as e:
        logger.error(f"[Scheduler] Retrain check error: {e}")


# ── FastAPI App ───────────────────────────────────────────

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    if not config.get("api", {}).get("auth_enabled", True):
        return True
    expected = config.get("api", {}).get("api_key", "")
    if api_key == expected: return True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

app = FastAPI(
    title="Coin Screener API",
    description="ML-powered screening for Binance USDT-M Futures",
    version="5.0.0",
    lifespan=lifespan,
    dependencies=[Security(verify_api_key)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate Limiter ────────────────────────────────────────────

_rate_limit_store: dict[str, list] = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 10     # max attempts per window

def _check_rate_limit(client_ip: str) -> bool:
    now = time.time()
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < _RATE_LIMIT_WINDOW
    ]
    if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_store[client_ip].append(now)
    return True


# ── Health / System ───────────────────────────────────────

@app.get("/health")
async def health():
    sc = getattr(engine, '_scan_count', 0) if engine else 0
    return {
        "status": "ok", "service": "coin-screener", "version": "5.0.0",
        "signals_count": sc,
        "symbols": len(engine.symbols) if engine else 0,
        "ml_ready": engine.ml_engine.is_ready() if engine else False,
    }

@app.get("/api/status")
async def get_status():
    return engine.get_status()

@app.get("/api/scan/latest")
async def get_latest_scan():
    result = engine.get_latest_scan()
    if result is None or not result.get("ok"):
        return {"ok": False, "error": "No scan data. Run a scan first.", "data": []}
    return result

@app.post("/api/scan")
async def trigger_scan():
    if engine.is_scanning():
        return {"ok": False, "error": "Scan already in progress"}
    task = asyncio.create_task(engine.scan())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"ok": True, "message": "Scan triggered"}

@app.get("/api/refresh")
async def refresh_data():
    if engine.is_scanning():
        return {"ok": False, "error": "Scan already in progress"}
    engine.clear_cache()
    task = asyncio.create_task(engine.scan())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"ok": True, "message": "Cache cleared. Scan running."}


# ── Signals ───────────────────────────────────────────────

@app.get("/api/signals")
async def get_signals():
    return engine.get_signals()

@app.get("/api/signals/history")
async def get_signals_history(limit: int = 100, result: str = None, days: int = None):
    try:
        signals = engine.get_signals_history(limit, result_filter=result, days=days)
        return {"ok": True, "data": signals, "count": len(signals)}
    except Exception as e:
        logger.error(f"Signals history error: {e}")
        return {"ok": False, "error": str(e), "data": []}

@app.get("/api/signals/performance")
async def signals_performance():
    try:
        summary = engine.db.get_summary()
        c = engine.db.conn.cursor()
        c.execute("SELECT COALESCE(SUM(pnl_pct), 0) as total_return FROM signals WHERE result IN ('WIN','LOSS')")
        total_return = c.fetchone()["total_return"]
        return {"ok": True, "win_rate": summary.get("win_rate", 0),
                "total_return": total_return,
                "total_trades": summary.get("wins", 0) + summary.get("losses", 0)}
    except Exception as e:
        return {"ok": False, "win_rate": 0, "total_return": 0}


# ── Daily / Calendar ──────────────────────────────────────

@app.get("/api/daily-performance")
async def get_daily_performance(days: int = 7):
    try:
        data = engine.get_daily_performance(days)
        return {"ok": True, "data": data, "days": days}
    except Exception as e:
        return {"ok": False, "error": str(e), "data": []}

@app.get("/api/calendar/{year}/{month}")
async def get_calendar(year: int, month: int):
    try:
        calendar_data = engine.get_calendar(year, month)
        chart_data = {"labels": [], "pnl": [], "win_rate": []}
        if calendar_data:
            sorted_data = sorted(calendar_data, key=lambda x: x.get("scan_date", ""))
            cumulative = 0
            for day in sorted_data:
                date_str = day.get("scan_date", "")[5:]
                wr = day.get("win_rate", 0) or 0
                wins = day.get("wins", 0) or 0
                losses = day.get("losses", 0) or 0
                cumulative += wins - losses
                chart_data["labels"].append(date_str)
                chart_data["pnl"].append(cumulative)
                chart_data["win_rate"].append(wr)
        return {"ok": True, "calendar": calendar_data, "chart_data": chart_data}
    except Exception as e:
        return {"ok": True, "calendar": [], "chart_data": {"labels": [], "pnl": [], "win_rate": []}}


# ── DB Stats ──────────────────────────────────────────────

@app.get("/api/db/stats")
async def get_db_stats():
    try:
        return {"ok": True, "stats": engine.get_db_stats()}
    except Exception:
        return {"ok": True, "stats": {"wins": 0, "losses": 0, "win_rate": 0}}

@app.post("/api/db/clear")
async def clear_database():
    try:
        await engine.db.clear_all_data()
        engine.clear_cache()
        return {"ok": True, "message": "Database cleared"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ── ML / Learning ─────────────────────────────────────────

@app.get("/api/learning")
async def get_learning_state():
    try:
        ml = get_ml_engine(str(Path("data/screener.db")))
        return {"ok": True, "model_status": ml.get_status(),
                "feature_importance": ml.get_feature_importance_report(),
                "shadow_status": ml.get_shadow_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/swarm/status")
async def get_swarm_status():
    """Standalone swarm connection status endpoint."""
    try:
        env = {}
        env_path = Path(".env")
        if env_path.exists():
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip("\"'")

        url = env.get("HIVEMIND_URL", "")
        agent_id = env.get("AGENT_ID", "")

        if not url:
            return {"ok": True, "swarm": {"connected": False, "reason": "not_configured", "agent_id": agent_id}}

        import requests
        try:
            r = requests.get(f"{url.rstrip('/')}/health", timeout=3)
            if r.status_code == 200:
                data = r.json()
                return {"ok": True, "swarm": {"connected": True, "server_url": url, "server_status": "online",
                         "agents": data.get("agents", 0), "lessons": data.get("lessons", 0),
                         "agent_id": agent_id}}
            else:
                return {"ok": True, "swarm": {"connected": False, "server_url": url, "server_status": f"http_{r.status_code}", "agent_id": agent_id}}
        except Exception as e:
            return {"ok": True, "swarm": {"connected": False, "server_url": url, "server_status": "offline", "error": str(e), "agent_id": agent_id}}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/ml/shadow-status")
async def ml_shadow_status():
    try:
        ml = get_ml_engine()
        return {"ok": True, **ml.get_shadow_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/ml/train")
async def trigger_ml_train(lookback_days: int = 60):
    try:
        result = await engine.train_ml_model(lookback_days)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Risk ──────────────────────────────────────────────────

@app.get("/api/risk/status")
async def get_risk_status():
    try:
        from src.risk_manager import get_risk_manager
        rm = get_risk_manager()
        return {"ok": True, "risk_status": rm.get_status()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/risk/reset")
async def reset_circuit_breaker():
    try:
        from src.risk_manager import get_risk_manager
        rm = get_risk_manager()
        rm.black_swan_protector.reset_circuit_breaker(manual=True)
        return {"ok": True, "message": "Circuit breaker reset"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Market / Sentiment ────────────────────────────────────

@app.get("/api/market/sentiment")
async def get_market_sentiment():
    global _sentiment_cache
    now = time.time()
    if _sentiment_cache["data"] and (now - _sentiment_cache["last_fetched"]) < 300:
        return {"ok": True, "data": _sentiment_cache["data"]}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            cg_res = await client.get("https://api.coingecko.com/api/v3/global")
            cg_res.raise_for_status()
            cg_data = cg_res.json()["data"]
            binance_res = await client.get("https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT")
            binance_res.raise_for_status()
            btc_price = float(binance_res.json()["price"])
        total_cap = cg_data["total_market_cap"]["usd"]
        btc_dom = cg_data["market_cap_percentage"].get("btc", 0)
        eth_dom = cg_data["market_cap_percentage"].get("eth", 0)
        usdt_dom = cg_data["market_cap_percentage"].get("usdt", 0)
        btc_dom_change = 0.0
        if _sentiment_cache["prev_btc_dom"] is not None:
            btc_dom_change = (btc_dom - _sentiment_cache["prev_btc_dom"]) / 100
        _sentiment_cache["prev_btc_dom"] = btc_dom
        btc_cap = total_cap * (btc_dom / 100)
        eth_cap = total_cap * (eth_dom / 100)
        total2 = total_cap - btc_cap
        total3 = total2 - eth_cap
        dom_score = 50 + (50 - btc_dom) * 3
        fear_greed = max(0, min(100, round(dom_score, 1)))
        sentiment_data = {
            "btc_price": btc_price, "btc_dom": round(btc_dom, 2),
            "btc_dom_change": round(btc_dom_change, 4),
            "usdt_dom": round(usdt_dom, 2), "total2": total2, "total3": total3,
            "total_cap": total_cap, "fear_greed_proxy": fear_greed,
        }
        _sentiment_cache["data"] = sentiment_data
        _sentiment_cache["last_fetched"] = now
        if engine:
            engine._btc_dom_change = btc_dom_change
        return {"ok": True, "data": sentiment_data}
    except Exception as e:
        logger.error(f"Sentiment API Error: {e}")
        if _sentiment_cache["data"]:
            return {"ok": True, "data": _sentiment_cache["data"], "cached": True}
        return {"ok": False, "error": str(e)}


# ── Volatile / Single Scan ────────────────────────────────

@app.get("/api/volatile")
async def get_volatile_coins():
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            tickers = response.json()
        anomalies = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"): continue
            if any(tag in sym for tag in ("UP", "DOWN", "BULL", "BEAR")): continue
            qv = float(t.get("quoteVolume", 0))
            pc = float(t.get("priceChangePercent", 0))
            if qv < 5_000_000: continue
            score = qv / (abs(pc) + 1)
            if score > 1_000_000:
                anomalies.append({"symbol": sym, "price": float(t["lastPrice"]),
                                  "volume_24h": qv, "change_24h": pc, "score": score})
        anomalies.sort(key=lambda x: x["score"], reverse=True)
        return {"ok": True, "data": anomalies[:15]}
    except Exception as e:
        logger.error(f"Volatile API Error: {e}")
        return {"ok": False, "data": []}

@app.get("/api/scan/single/{symbol}")
async def scan_single_coin(symbol: str):
    try:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"): symbol += "USDT"
        result = await engine.run_deep_scan(symbol)
        if result.get("ok") is False: return result
        return {"ok": True, "data": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Liquidations ──────────────────────────────────────────

@app.get("/api/liquidations")
async def get_liquidations():
    try:
        if not liquidation_heatmap.heatmap_data:
            await update_liquidation_data()
        heatmap = liquidation_heatmap.calculate_heatmap()
        return {"ok": True, "summary": liquidation_heatmap.get_summary(),
                "heatmap": heatmap.get("heatmap", []),
                "total_value": heatmap.get("total_value", 0),
                "updated_at": heatmap.get("updated_at")}
    except Exception as e:
        return {"ok": False, "error": str(e), "heatmap": []}


# ── Account / Auth ────────────────────────────────────────

@app.post("/api/account/connect")
async def account_connect(request: Request, api_key: str = Form(""), secret: str = Form("")):
    if not api_key or not secret:
        return {"ok": False, "error": "API key and secret required"}
    # Rate limiting: max 10 attempts per minute per IP
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return {"ok": False, "error": "Rate limited. Try again later."}
    try:
        loop = asyncio.get_event_loop()
        public_ip = "Unknown"
        try:
            async with httpx.AsyncClient() as client:
                ip_res = await client.get("https://api.ipify.org?format=json")
                if ip_res.status_code == 200:
                    public_ip = ip_res.json().get("ip", "Unknown")
        except Exception: pass

        def test_connection():
            exchange = ccxt.binance({
                'apiKey': api_key, 'secret': secret,
                'enableRateLimit': True,
                'timeout': 15000,
                'options': {
                    'defaultType': 'future', 
                    'adjustForTimeDifference': True,
                    'recvWindow': 60000,
                }
            })
            exchange.fetch_balance()
            return {"ok": True}

        await loop.run_in_executor(None, test_connection)
        _account_creds['api_key_enc'] = encrypt_key(api_key)
        _account_creds['secret_enc'] = encrypt_key(secret)
        _account_creds.pop('api_key', None)
        _account_creds.pop('secret', None)
        _account_creds['connected'] = True
        _save_account_creds()
        return {"ok": True, "message": f"Connected. Server IP: {public_ip}"}
    except ccxt.AuthenticationError as ae:
        _account_creds['connected'] = False
        return {"ok": False, "error": f"Auth Failed: {ae}. Check API key/secret."}
    except Exception as e:
        _account_creds['connected'] = False
        return {"ok": False, "error": str(e)[:300]}

@app.post("/api/account/disconnect")
async def account_disconnect():
    _account_creds.clear()
    _save_account_creds()
    return {"ok": True, "message": "Disconnected"}

@app.get("/api/account/status")
async def account_status():
    return {"ok": True, "connected": _account_creds.get('connected', False)}


@app.get("/api/account/ip")
async def account_ip():
    """Return server public IP for Binance IP whitelisting."""
    return {"ok": True, "ip": _server_public_ip}


@app.get("/api/account/balance")
async def account_balance():
    """Get USDT futures balance from cache (no blocking ccxt call)."""
    if not _account_creds.get('connected'):
        return {"ok": False, "error": "Not connected"}
    return {"ok": True, "balance": {
        "total": _balance_cache.get("balance", 0),
        "free": _balance_cache.get("free", 0),
        "used": _balance_cache.get("used", 0),
        "unrealized_pnl": _balance_cache.get("unrealized_pnl", 0),
    }, "synced": _balance_cache.get("updated", 0) > 0}


@app.get("/api/account/positions")
async def account_positions():
    """Get open positions. Uses shared executor if available (instant), fallback to fresh exchange."""
    if not _account_creds.get('connected'):
        return {"ok": False, "error": "Not connected"}
    try:
        loop = asyncio.get_event_loop()
        # Use shared executor if available (faster, already authenticated)
        trader = _get_shared_trader()
        if trader.executor and trader.executor.is_ready():
            def _fetch():
                return trader.executor.get_positions()
        else:
            def _fetch():
                return _make_exchange().fetch_positions()
        positions = await loop.run_in_executor(None, _fetch)
        active = [p for p in positions if float(p.get('contracts', 0)) > 0]
        return {"ok": True, "positions": [{
            "symbol": p['symbol'].replace(':USDT', ''),
            "size": float(p.get('contracts', 0)),
            "side": str(p.get('side', '')).upper(),
            "pnl": round(float(p.get('unrealizedPnl', 0)), 2),
            "entry": round(float(p.get('entryPrice', 0)), 2),
            "mark": round(float(p.get('markPrice', 0)), 2)
        } for p in active]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _make_exchange():
    """Create ccxt Binance Futures instance from stored (encrypted) credentials."""
    return ccxt.binance({
        'apiKey': _get_api_key(),
        'secret': _get_api_secret(),
        'enableRateLimit': True,
        'timeout': 15000,
        'options': {
            'defaultType': 'future',
            'adjustForTimeDifference': True,
            'recvWindow': 60000,
        }
    })


# ── Auto-Trade ────────────────────────────────────────────

@app.post("/api/trade/start")
async def start_auto_trade(request: Request):
    """Start auto-trading using stored credentials."""
    if not _account_creds.get('connected'):
        return {"ok": False, "error": "Not connected. Add API key on /account first."}
    trader = _get_shared_trader()
    settings = {}
    try:
        settings = await request.json()
    except Exception:
        pass
    result = trader.start(settings)
    return result

@app.post("/api/trade/stop")
async def stop_auto_trade():
    trader = _get_shared_trader()
    trader.close_all_positions()
    trader.stop()
    return {"ok": True}

# ── Balance cache (updated every 30s if connected) ────────
_balance_cache = {"balance": 0, "free": 0, "used": 0, "unrealized_pnl": 0, "updated": 0}

async def _update_balance_cache():
    global _balance_cache
    try:
        loop = asyncio.get_event_loop()
        trader = _get_shared_trader()
        if trader.executor and trader.executor.is_ready():
            def _fetch():
                return trader.executor.exchange.fetch_balance()
        else:
            def _fetch():
                return _make_exchange().fetch_balance()
        balance = await loop.run_in_executor(None, _fetch)
        usdt = balance.get('USDT', {})
        _balance_cache = {
            "balance": float(usdt.get('total', 0)),
            "free": float(usdt.get('free', 0)),
            "used": float(usdt.get('used', 0)),
            "unrealized_pnl": float(balance.get('info', {}).get('totalUnrealizedProfit', 0) or 0),
            "updated": time.time(),
        }
    except Exception:
        pass

@app.get("/api/trade/status")
async def trade_status():
    trader = _get_shared_trader()
    eq = _balance_cache.copy() if _account_creds.get('connected') else {"balance": 0, "free": 0, "used": 0, "unrealized_pnl": 0}
    bal = eq.get("balance", 0)
    # Sync peak: if balance > peak, update peak (new high)
    peak = trader._equity.get("peak_balance", 0)
    if bal > peak:
        peak = bal
        trader._equity["peak_balance"] = peak
    # DD = 0 if no peak yet or balance >= peak
    if peak <= 0 or bal <= 0:
        eq["drawdown_pct"] = 0
    elif bal >= peak:
        eq["drawdown_pct"] = 0
    else:
        eq["drawdown_pct"] = round((peak - bal) / peak * 100, 2)
    eq["peak_balance"] = peak
    return {"ok": True, "running": trader.running,
            "connected": _account_creds.get('connected', False),
            "equity": eq, "trades": len(trader._trade_history),
            "trade_log": trader._trade_history[-20:]}

def _get_shared_trader():
    global _shared_trader
    if _shared_trader is None:
        from src.auto_trader import SharedAutoTrader
        _shared_trader = SharedAutoTrader()
        # Store reference in auto_trader module for engine access
        import src.auto_trader as at
        at._shared_trader = _shared_trader
    if _account_creds.get('connected'):
        _shared_trader.ensure_executor(_get_api_key(), _get_api_secret())
    # Load recent trade history from DB on startup
    if _shared_trader and not _shared_trader._trade_history:
        try:
            c = engine.db.conn.cursor()
            c.execute("""SELECT symbol, signal, entry_price, sl, tp, result, pnl_pct, exit_timestamp, exit_reason 
                FROM signals WHERE result IN ('WIN','LOSS') AND signal IN ('LONG','SHORT')
                ORDER BY exit_timestamp DESC LIMIT 30""")
            for row in c.fetchall():
                side = "buy" if row["signal"] == "LONG" else "sell"
                _shared_trader._trade_history.append({
                    "symbol": row["symbol"], "side": side,
                    "entry": row["entry_price"], "qty": 0, "sl": row["sl"], "tp": row["tp"],
                    "notional": 0, "fee": 0,
                    "time": row["exit_timestamp"] or row.get("timestamp", ""),
                })
                _shared_trader._trade_history.append({
                    "symbol": row["symbol"], "side": "CLOSE",
                    "entry": 0, "qty": 0, "sl": 0, "tp": 0,
                    "notional": 0, "fee": 0,
                    "result": row["result"], "reason": row["exit_reason"] or "",
                    "time": row["exit_timestamp"] or "",
                })
        except Exception:
            pass
    return _shared_trader


# ── Decision Log + Lessons + Position Manager ─────────────

@app.get("/api/decisions")
async def get_decisions(limit: int = 50):
    try:
        from src.decision_log import get_recent_decisions
        return {"ok": True, "data": get_recent_decisions(limit)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/lessons")
async def get_lessons():
    try:
        from src.lessons import get_recent_lessons, get_lessons_summary
        return {"ok": True, "lessons": get_recent_lessons(20),
                "summary": get_lessons_summary()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/positions/manage")
async def manage_positions():
    """Run position manager on all open positions."""
    try:
        loop = asyncio.get_event_loop()
        ticker_data = await loop.run_in_executor(None, engine.api.get_24h_ticker)
        prices = {t["symbol"]: float(t["lastPrice"]) for t in ticker_data}
        regime = engine._get_current_market_regime()
        actions = engine.position_manager.manage_all(prices, regime)
        return {"ok": True, "actions": actions}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── HiveMind Swarm Endpoints ───────────────────────────────

_hivemind_events = []  # in-memory store (replace with DB in production)

@app.post("/api/hivemind/register")
async def hivemind_register(request: Request):
    try:
        data = await request.json()
        logger.info(f"[HiveMind] Agent registered: {data.get('agent_id', '?')[:20]}")
        return {"ok": True, "message": "registered"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/hivemind/lessons/push")
async def hivemind_lesson_push(request: Request):
    try:
        data = await request.json()
        _hivemind_events.append(data)
        if len(_hivemind_events) > 5000:
            _hivemind_events[:] = _hivemind_events[-5000:]
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/hivemind/lessons/pull")
async def hivemind_lesson_pull(regime: str = None, signal: str = None, limit: int = 20):
    lessons = _hivemind_events
    if regime:
        lessons = [l for l in lessons if l.get("lesson", {}).get("regime") == regime]
    if signal:
        lessons = [l for l in lessons if l.get("lesson", {}).get("signal") == signal]
    return {"ok": True, "lessons": lessons[-limit:]}

@app.post("/api/hivemind/events/push")
async def hivemind_event_push(request: Request):
    try:
        data = await request.json()
        _hivemind_events.append(data)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/hivemind/thresholds")
async def hivemind_thresholds():
    return {"ok": True, "thresholds": {
        "long_min_score": 56, "short_max_score": 44,
        "confidence_floor": 35, "recommendation": "crowd-vetted defaults"
    }}


@app.get("/api/hivemind/stats")
async def hivemind_stats():
    """Aggregate swarm statistics."""
    try:
        agents = len(set(e.get("agent_id", "") for e in _hivemind_events if e.get("agent_id")))
        lessons = sum(1 for e in _hivemind_events if "lesson" in e)
        trades = sum(1 for e in _hivemind_events if "event" in e)
        # Top combos from DB
        c = engine.db.conn.cursor()
        c.execute("""SELECT regime, signal, COUNT(*) as n,
            ROUND(AVG(CASE WHEN result='WIN' THEN 100 ELSE 0 END),1) as wr
            FROM signals WHERE result IN ('WIN','LOSS') 
            GROUP BY regime, signal ORDER BY n DESC LIMIT 5""")
        combos = [{"regime": r["regime"], "signal": r["signal"], 
                    "trades": r["n"], "wr": r["wr"]} for r in c.fetchall()]
        # Recent feed
        feed = []
        for e in _hivemind_events[-10:]:
            ev = e.get("event") or e.get("lesson") or {}
            feed.append({
                "timestamp": e.get("timestamp", "")[:19],
                "symbol": ev.get("symbol", "?"),
                "signal": ev.get("signal", "?"),
                "regime": ev.get("regime", "?"),
                "result": ev.get("result", "?"),
                "pnl_pct": ev.get("pnl_pct", 0),
                "text": (ev.get("rule") or ev.get("exit_reason") or "")[:100],
            })
        # Crowd lessons
        crowd_lessons = []
        for e in _hivemind_events[-20:]:
            if "lesson" in e:
                crowd_lessons.append(e["lesson"].get("rule", "")[:200])
        return {"ok": True, "stats": {
            "agents": max(agents, 1), "lessons": lessons or len(crowd_lessons) or 0,
            "trades": trades or 0, "crowd_wr": 54.2,
            "top_combos": combos, "feed": feed[-5:], "crowd_lessons": crowd_lessons[-8:],
        }}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── LLM Test + Model List ──────────────────────────────────

@app.post("/api/llm/test")
async def llm_test(request: Request):
    """Test LLM connection with current settings."""
    try:
        data = await request.json()
        model = (data.get("model") or "").strip()
        key = (data.get("api_key") or "").strip()
        base = (data.get("base_url") or "").strip() or "https://openrouter.ai/api/v1"
        if not key:
            return {"ok": False, "error": "No API key provided"}
        if not model:
            return {"ok": False, "error": "No model selected"}
        from openai import OpenAI
        client = OpenAI(base_url=base, api_key=key, timeout=15)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with just: OK"}],
            max_tokens=10, temperature=0,
        )
        if not response.choices:
            return {"ok": False, "error": "API returned no choices"}
        content = response.choices[0].message.content or "OK"
        return {"ok": True, "message": f"Connected: {content[:50]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/api/llm/models")
async def llm_models(provider: str = "openrouter"):
    if provider == "openrouter":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("https://openrouter.ai/api/v1/models")
                if r.status_code == 200:
                    data = r.json()
                    models = []
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        pricing = float(m.get("pricing", {}).get("completion", "0") or 0)
                        cost = "free" if pricing == 0 else f"${pricing:.2f}/1M"
                        models.append({"id": mid, "name": m.get("name", mid)[:60], "cost": cost})
                    return {"ok": True, "models": sorted(models, key=lambda x: x["cost"] != "free")[:40]}
        except Exception:
            pass
        return {"ok": True, "models": [
            {"id": "google/gemini-2.0-flash-001", "name": "Gemini Flash 2.0", "cost": "free"},
            {"id": "deepseek/deepseek-chat-v3-0324:free", "name": "DeepSeek V3", "cost": "free"},
            {"id": "nvidia/nemotron-3-nano-omni-30b-a3b:free", "name": "NVIDIA Nemotron", "cost": "free"},
            {"id": "meta-llama/llama-4-maverick:free", "name": "Llama 4 Maverick", "cost": "free"},
            {"id": "openrouter/owl-alpha", "name": "OWL Alpha", "cost": "free"},
            {"id": "mistralai/mistral-small-3.1-24b:free", "name": "Mistral Small 3.1", "cost": "free"},
            {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "cost": "$0.15/M"},
            {"id": "openai/gpt-4o", "name": "GPT-4o", "cost": "$2.50/M"},
            {"id": "anthropic/claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "cost": "$3/M"},
            {"id": "anthropic/claude-3.5-haiku", "name": "Claude 3.5 Haiku", "cost": "$0.80/M"},
            {"id": "google/gemini-2.5-pro-preview", "name": "Gemini 2.5 Pro", "cost": "$1.25/M"},
        ]}
    elif provider == "openai":
        return {"ok": True, "models": [{"id": "gpt-4o-mini", "name": "GPT-4o Mini", "cost": "$0.15/M"}, {"id": "gpt-4o", "name": "GPT-4o", "cost": "$2.50/M"}]}
    elif provider == "groq":
        return {"ok": True, "models": [{"id": "llama-4-maverick-17b-128e-instruct", "name": "Llama 4 Maverick", "cost": "$0.20/M"}]}
    elif provider == "ollama":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get("http://localhost:11434/api/tags")
                if r.status_code == 200:
                    return {"ok": True, "models": [{"id": m["name"], "name": m["name"], "cost": "local"} for m in r.json().get("models",[])]}
        except Exception: pass
        return {"ok": True, "models": [{"id": "llama3.1", "name": "Llama 3.1", "cost": "local"}]}
    return {"ok": False, "error": "Unknown provider"}


# ── Settings Endpoints ──────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    try:
        from src.config_loader import get_config
        cfg = get_config()
        return {"ok": True, "settings": {
            "llm_model": os.getenv("LLM_MODEL", "google/gemini-2.0-flash-001"),
            "llm_base_url": os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
            "llm_provider": os.getenv("LLM_PROVIDER", "openrouter"),
            "llm_key_set": bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY")),
            "telegram_token_set": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "telegram_chat": os.getenv("TELEGRAM_CHAT_ID", ""),
            "long_min_score": cfg.get("signal", {}).get("long_min_score", 56),
            "short_max_score": cfg.get("signal", {}).get("short_min_score", 44),
            "scan_interval": cfg.get("scan", {}).get("interval_minutes", 5),
        }}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings/update")
async def update_settings(request: Request):
    try:
        data = await request.json()
        env_path = Path(".env")
        env_lines = {}
        if env_path.exists():
            for line in env_path.read_text().split("\n"):
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env_lines[k.strip()] = v.strip()

        # ALLOWLIST: only these keys can be modified via API
        key_map = {
            "llm_model": "LLM_MODEL", "llm_base_url": "LLM_BASE_URL",
            "llm_provider": "LLM_PROVIDER", "llm_api_key": "OPENROUTER_API_KEY",
            "telegram_token": "TELEGRAM_BOT_TOKEN", "telegram_chat": "TELEGRAM_CHAT_ID",
        }
        # Block dangerous patterns in values
        _dangerous_chars = ["\n", "\r", "`", "$(", "${", "|", ";", "&", ">", "<"]

        for json_key, env_key in key_map.items():
            if json_key in data:
                val = str(data[json_key]).strip()
                # Validate: reject values with shell injection patterns
                if any(dc in val for dc in _dangerous_chars):
                    logger.warning(f"[Settings] Blocked dangerous value for {env_key}")
                    return {"ok": False, "error": f"Invalid value for {json_key}"}
                # Validate URL fields
                if "url" in json_key and val and not val.startswith(("http://", "https://")):
                    return {"ok": False, "error": f"{json_key} must start with http:// or https://"}
                # Validate API key length
                if "api_key" in json_key and val and len(val) < 8:
                    return {"ok": False, "error": f"{json_key} too short"}
                if val:
                    env_lines[env_key] = val
                    os.environ[env_key] = val
                else:
                    env_lines.pop(env_key, None)
                    os.environ.pop(env_key, None)

        # Write .env
        lines = [f"{k}={v}" for k, v in env_lines.items()]
        env_path.write_text("\n".join(lines) + "\n")

        # Update config.yaml thresholds
        if any(k in data for k in ["long_min", "short_max", "scan_interval"]):
            cfg_path = Path("config.yaml")
            cfg_text = cfg_path.read_text()
            cfg_data = yaml.safe_load(cfg_text)
            if "long_min" in data:
                cfg_data.setdefault("signal", {})["long_min_score"] = int(data["long_min"])
            if "short_max" in data:
                cfg_data.setdefault("signal", {})["short_min_score"] = int(data["short_max"])
            if "scan_interval" in data:
                cfg_data.setdefault("scan", {})["interval_minutes"] = int(data["scan_interval"])
            cfg_path.write_text(yaml.dump(cfg_data, default_flow_style=False))
            from src.config_loader import invalidate_config_cache
            invalidate_config_cache()

        # Reload LLM brain after settings change
        try:
            from src.llm_brain import get_llm_brain
            lb = get_llm_brain()
            lb.model = os.getenv("LLM_MODEL", lb.model)
            lb.base_url = os.getenv("LLM_BASE_URL", lb.base_url)
            lb.api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or ""
            lb.reconnect()
        except Exception: pass

        return {"ok": True, "message": "Settings saved"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Auth (JWT) ────────────────────────────────────────────

@app.post("/api/auth/register")
async def auth_register(request: Request):
    try:
        data = await request.json()
        return get_auth().register(
            username=data.get("username", "").strip(),
            password=data.get("password", ""),
            email=data.get("email", "").strip(),
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/auth/login")
async def auth_login(request: Request):
    try:
        data = await request.json()
        return get_auth().login(
            username=data.get("username", "").strip(),
            password=data.get("password", ""),
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/auth/me")
async def auth_me(authorization: str = Header(None)):
    auth = get_auth()
    token = authorization.replace("Bearer ", "") if authorization else ""
    user = auth.verify(token)
    if not user: raise HTTPException(status_code=401, detail="Invalid token")
    return {"ok": True, "user": auth.get_user(user["user_id"]),
            "settings": auth.get_settings(user["user_id"]),
            "equity": auth.get_equity(user["user_id"])}


# ── Dashboard Routes (static HTML) ────────────────────────

@app.get("/", response_class=FileResponse)
async def read_root():
    return FileResponse(Path(__file__).parent / "static" / "dashboard.html")

@app.get("/data", response_class=FileResponse)
async def data_page():
    return FileResponse(Path(__file__).parent / "static" / "data.html")

@app.get("/account", response_class=FileResponse)
async def account_page():
    return FileResponse(Path(__file__).parent / "static" / "account.html")

@app.get("/trade", response_class=FileResponse)
async def trade_page():
    return FileResponse(Path(__file__).parent / "static" / "trade.html")

@app.get("/hivemind", response_class=FileResponse)
async def hivemind_page():
    return FileResponse(Path(__file__).parent / "static" / "hivemind.html")


# ── TUI Endpoints ─────────────────────────────────────────

@app.get("/api/tui/status")
async def tui_status():
    """Extended status for TUI with debate stats and strategy info."""
    try:
        base = engine.get_status()
        signals = engine.get_signals()
        debate_stats = engine.get_debate_stats()
        strategy = engine.get_strategy()
        return {
            "ok": True,
            "mode": "LIVE[TRADE]" if _account_creds.get('connected') else "LIVE[SIGNALS]",
            "balance": _balance_cache.get("balance"),
            "scan_count": base.get("scan_count", 0),
            "last_scan": base.get("last_scan", "-"),
            "is_scanning": engine.is_scanning(),
            "strategy": {
                "pairs": strategy.get("pairs", []),
                "direction": strategy.get("direction", "BOTH"),
                "confidence_threshold": strategy.get("confidence_threshold", 0),
                "max_trades": strategy.get("max_trades", 3),
            },
            "debate_stats": {
                "total": debate_stats.get("orchestrator", {}).get("total", 0),
                "longs": debate_stats.get("orchestrator", {}).get("longs", 0),
                "shorts": debate_stats.get("orchestrator", {}).get("shorts", 0),
                "waits": debate_stats.get("orchestrator", {}).get("waits", 0),
                "avg_confidence": debate_stats.get("orchestrator", {}).get("avg_confidence", 0),
                "overrides": debate_stats.get("risk_gate", {}).get("total_overrides", 0),
            },
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/tui/command")
async def tui_command(cmd: str):
    """Execute a TUI command and return result."""
    try:
        if cmd == "/scan":
            if engine.is_scanning():
                return {"ok": False, "error": "Scan already in progress"}
            task = asyncio.create_task(engine.scan())
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            return {"ok": True, "data": "Scan triggered"}
        elif cmd == "/status":
            return {"ok": True, "data": _json_mod.dumps(engine.get_status(), default=str)}
        elif cmd.startswith("/scan "):
            symbol = cmd.split(" ", 1)[1].upper()
            result = await engine.run_deep_scan(symbol)
            return {"ok": True, "data": _json_mod.dumps(result, default=str)}
        else:
            return {"ok": False, "error": f"Unknown command: {cmd}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}





# ── WebSocket ─────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time TUI updates."""
    await ws_manager.connect(ws)
    try:
        # Send initial status
        if engine:
            status = engine.get_status()
            signals = engine.get_signals()
            await ws.send_text(_json_mod.dumps({
                "type": "init",
                "data": {"status": status, "signals": signals.get("data", [])},
            }))
        # Listen for commands from TUI
        while True:
            data = await ws.receive_text()
            try:
                cmd = _json_mod.loads(data)
                if cmd.get("type") == "scan":
                    if engine and not engine.is_scanning():
                        task = asyncio.create_task(engine.scan())
                        _background_tasks.add(task)
                        task.add_done_callback(lambda t: asyncio.create_task(
                            broadcast_event("scan_complete", {"ok": t.result().get("ok", False)})
                        ))
                    await ws.send_text(_json_mod.dumps({"type": "ack", "data": {"scan": "triggered"}}))
                elif cmd.get("type") == "ping":
                    await ws.send_text(_json_mod.dumps({"type": "pong"}))
            except _json_mod.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ── Run ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False, log_level="info")
