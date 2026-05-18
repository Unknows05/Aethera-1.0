"""
Auto-Trader — Per-user automated trading orchestrator.

Manages the lifecycle per user:
1. Load encrypted API keys
2. Extract signals from screening engine
3. Apply risk tiers + position sizing
4. Execute orders via ccxt
5. Monitor open positions
6. Record P&L
"""
import asyncio
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False


class AutoTrader:
    def __init__(self, user_id: int, auth, engine=None, db_path: str = "data/screener.db"):
        self.user_id = user_id
        self.auth = auth
        self.engine = engine
        self.executor = None
        self.running = False
        self._task: Optional[asyncio.Task] = None

    def is_ready(self) -> bool:
        return self.executor is not None and self.executor.is_ready()

    def start(self):
        if self.running:
            return {"ok": False, "error": "Already running"}
        keys_list = self.auth.get_api_keys(self.user_id)
        if not keys_list:
            return {"ok": False, "error": "No API keys configured"}

        from src.key_manager import decrypt_key
        key_id = keys_list[0]["id"]
        conn = self.auth._get_conn()
        c = conn.cursor()
        c.execute(
            "SELECT api_key_encrypted, secret_encrypted, testnet FROM user_api_keys WHERE id = ?",
            (key_id,))
        row = c.fetchone()
        conn.close()

        if not row:
            return {"ok": False, "error": "API key not found"}

        api_key = decrypt_key(row["api_key_encrypted"])
        secret = decrypt_key(row["secret_encrypted"])
        testnet = bool(row["testnet"])

        from src.execution import TradeExecutor
        self.executor = TradeExecutor(api_key, secret, testnet)
        if not self.executor.is_ready():
            return {"ok": False, "error": "Exchange connection failed (ccxt not installed or invalid keys)"}

        self.running = True
        mode = "TESTNET" if testnet else "LIVE"
        logger.info(f"[AutoTrader] User {self.user_id} started {mode}")
        return {"ok": True, "mode": mode}

    def stop(self):
        self.running = False
        if self.executor:
            self.executor.close()
        self.executor = None
        settings = self.auth.get_settings(self.user_id)
        if settings:
            self.auth.update_settings(self.user_id, {"auto_trade_enabled": 0})
        logger.info(f"[AutoTrader] User {self.user_id} stopped")

    def execute_signal(self, signal: Dict) -> Dict:
        if not self.is_ready() or not self.running:
            return {"ok": False, "error": "Trader not running"}

        settings = self.auth.get_settings(self.user_id)
        equity = self.auth.get_equity(self.user_id)

        # Check drawdown circuit breaker
        dd = equity.get("drawdown_pct", 0)
        max_dd = settings.get("stop_trading_at_dd", 20.0)
        if dd >= max_dd:
            return {"ok": False, "error": f"Drawdown {dd:.1f}% >= max {max_dd:.0f}%"}

        from src.position_sizing import calculate_adaptive_position_size

        capital = equity.get("balance", 100)
        target = settings.get("target_capital", 10000)
        entry = float(signal.get("entry", signal.get("price", 0)))
        sl = float(signal.get("sl", 0))
        pair = signal.get("symbol", "")
        direction = signal.get("signal", "WAIT")
        confidence = float(signal.get("confidence", 50))

        # Estimate WR from ML confidence or signal confidence
        wr = confidence / 100.0
        rr = float(signal.get("atr_tp_mult", 3.0)) / float(signal.get("atr_sl_mult", 1.5))

        if entry <= 0 or sl <= 0:
            return {"ok": False, "error": "Missing entry/SL prices"}

        sizing = calculate_adaptive_position_size(
            capital=capital, target_capital=target,
            entry_price=entry, stop_loss_price=sl,
            win_rate=wr, rr_ratio=rr, pair=pair, use_maker=True,
        )

        if sizing.get("blocked"):
            return {"ok": False, "error": sizing.get("block_reason", "Min notional")}

        notional = sizing["notional"]
        qty = sizing["contracts"]
        if notional <= 0 or qty <= 0:
            return {"ok": False, "error": "Position size too small"}

        side = "buy" if direction == "LONG" else "sell"

        sl_price = float(signal.get("sl", 0))
        tp_price = float(signal.get("tp", 0))

        order = self.executor.place_oco_order(
            symbol=pair, side=side, quantity=qty,
            price=entry, stop_price=sl_price, limit_price=tp_price,
        )

        if order is None:
            return {"ok": False, "error": "Order placement failed"}

        trade_id = self.auth.save_trade(
            self.user_id, signal.get("id", 0), pair, entry, qty
        )

        return {
            "ok": True, "trade_id": trade_id, "symbol": pair,
            "entry": entry, "qty": qty, "notional": notional,
            "sl": sl_price, "tp": tp_price, "order_id": order.get("id"),
        }

    def check_open_positions(self, prices: Dict[str, float]):
        if not self.is_ready() or not self.running:
            return

        open_trades = self.auth.get_open_trades(self.user_id)
        for trade in open_trades:
            symbol = trade["symbol"]
            entry = trade["entry_price"]
            current = prices.get(symbol)
            if not current:
                continue

            pnl_pct = (current - entry) / entry * 100
            pnl_usd = trade["quantity"] * (current - entry)

            self.auth.close_trade(
                trade["id"], current, pnl_usd, pnl_pct, "MARK_TO_MARKET"
            )

        balance = self.executor.get_balance()
        if balance is not None:
            self.auth.update_equity(self.user_id, balance)

    def process_scan_signals(self, signals: List[Dict]):
        if not self.is_ready() or not self.running:
            return

        settings = self.auth.get_settings(self.user_id)
        if not settings.get("auto_trade_enabled", 0):
            return

        open_trades = self.auth.get_open_trades(self.user_id)
        open_symbols = {t["symbol"] for t in open_trades}

        for signal in signals:
            if signal.get("signal") not in ("LONG", "SHORT"):
                continue
            if signal.get("risk_blocked"):
                continue
            if signal.get("symbol") in open_symbols:
                continue

            confidence = signal.get("confidence", 50)
            ml_conf = signal.get("ml_confidence", 50)
            if ml_conf < 55 or confidence < 50:
                continue

            result = self.execute_signal(signal)
            if result.get("ok"):
                logger.info(
                    f"[AutoTrader] User {self.user_id}: {signal['signal']} "
                    f"{signal['symbol']} @ {result.get('entry')}"
                )

    def close_all_positions(self):
        if not self.is_ready():
            return
        open_trades = self.auth.get_open_trades(self.user_id)
        for trade in open_trades:
            self.executor.close_position(trade["symbol"])
            self.auth.close_trade(trade["id"], 0, 0, 0, "MANUAL_CLOSE")


_auto_traders: Dict[int, AutoTrader] = {}
_shared_trader: Optional["SharedAutoTrader"] = None


def get_auto_trader(user_id: int, auth, engine=None) -> AutoTrader:
    global _auto_traders
    if user_id not in _auto_traders:
        _auto_traders[user_id] = AutoTrader(user_id, auth, engine)
    return _auto_traders[user_id]


class SharedAutoTrader:
    """Single-user auto-trader using globally stored credentials."""

    def __init__(self):
        self.executor = None
        self.running = False
        self._settings = {}
        self._equity = {"balance": 100, "drawdown_pct": 0, "peak_balance": 100}
        self._trade_history = []

    def ensure_executor(self, api_key: str, secret: str):
        if self.executor is not None:
            return
        from src.execution import TradeExecutor
        self.executor = TradeExecutor(api_key, secret, testnet=False)
        # Fetch real balance on init
        try:
            balance = self.executor.get_balance()
            if balance is not None and balance > 0:
                self._equity["balance"] = balance
                self._equity["peak_balance"] = balance
                logger.info(f"[SharedAutoTrader] Balance: ${balance:.2f}")
                # Small capital mode
                if balance < 50:
                    logger.warning(f"[SharedAutoTrader] SMALL CAPITAL MODE (${balance:.2f}) — micro pairs only")
        except Exception as e:
            logger.debug(f"[SharedAutoTrader] Balance fetch deferred: {e}")

    def start(self, settings: dict = None) -> dict:
        if self.running:
            return {"ok": False, "error": "Already running"}
        if self.executor is None:
            return {"ok": False, "error": "No credentials. Connect API key in /account first."}
        if not self.executor.is_ready():
            return {"ok": False, "error": "Exchange connection failed. Check API key permissions."}
        self.running = True
        if settings:
            self._settings = settings
        try:
            balance = self.executor.get_balance()
            if balance is not None:
                self._equity["balance"] = balance
                self._equity["peak_balance"] = max(self._equity.get("peak_balance", 0), balance)
        except Exception:
            pass
        # Detect existing open positions on exchange
        try:
            positions = self.executor.get_positions()
            active = [p for p in positions if float(p.get('contracts', 0)) > 0]
            if active:
                logger.info(f"[SharedAutoTrader] Resumed with {len(active)} existing positions")
                for p in active:
                    sym = p['symbol'].replace(':USDT', '')
                    logger.info(f"  {sym}: {p.get('side','')} size={p.get('contracts','')} entry={p.get('entryPrice','')}")
        except Exception:
            pass
        logger.info("[SharedAutoTrader] Started")
        return {"ok": True, "mode": "LIVE"}

    def stop(self):
        self.running = False
        logger.info("[SharedAutoTrader] Stopped")

    def close_all_positions(self):
        if self.executor:
            self.executor.close_position(None)

    def process_scan_signals(self, signals: List[Dict], prices: Dict[str, float] = None):
        """LLM-aware signal execution with small-capital support."""
        if not self.running or self.executor is None:
            return
        if not signals:
            return

        min_conf = self._settings.get("min_confidence", 55)
        max_lev = self._settings.get("max_leverage", 5)
        risk_pct = self._settings.get("risk_per_trade_pct", 5) / 100.0
        capital = self._equity.get("balance", 100)

        # Small capital mode: auto-adjust
        if capital < 50:
            max_lev = min(max_lev, 10)  # higher leverage for small capital
            risk_pct = min(risk_pct, 0.05)  # max 5% risk
            logger.info(f"[AutoTrader] Small cap (${capital:.2f}): lev={max_lev}x, risk={risk_pct*100:.1f}%")

        for signal in signals:
            if signal.get("signal") not in ("LONG", "SHORT"):
                continue
            if signal.get("risk_blocked"):
                continue

            conf = signal.get("confidence", 50)
            # LLM-decided signals get priority — use LLM confidence adjustment
            if signal.get("llm_rationale"):
                conf += signal.get("confidence_adjustment", 0)
                logger.info(f"[AutoTrader] LLM decision: {signal['symbol']} {signal['signal']} ({signal.get('llm_rationale','')[:80]})")

            if conf < min_conf:
                continue

            entry = float(signal.get("entry", signal.get("price", 0)))
            sl = float(signal.get("sl", 0))
            tp = float(signal.get("tp", 0))
            pair = signal.get("symbol", "")
            direction = signal.get("signal")

            if entry <= 0 or sl <= 0:
                continue

            from src.position_sizing import calculate_position_size, BINANCE_MIN_NOTIONAL

            # Small capital: skip pairs with high min notional
            min_notional = BINANCE_MIN_NOTIONAL.get(pair, 20)
            max_notional = capital * max_lev
            if max_notional * 0.3 < min_notional:
                logger.debug(f"[AutoTrader] Skip {pair}: buying power ${max_notional:.0f} < min ${min_notional}")
                continue

            sizing = calculate_position_size(
                capital=capital, risk_pct=risk_pct,
                entry_price=entry, stop_loss_price=sl,
                leverage=max_lev, use_maker=True,
            )

            notional = sizing.get("notional", 0)
            qty = sizing.get("contracts", 0)
            fee_cost = sizing.get("fee_cost_usd", 0)

            if notional < 10 or qty <= 0:
                continue

            # Fee check: don't trade if fees > 5% of risk
            if capital < 50 and fee_cost > capital * risk_pct * 0.5:
                logger.debug(f"[AutoTrader] Skip {pair}: fees ${fee_cost:.2f} too high for ${capital:.2f}")
                continue

            side = "buy" if direction == "LONG" else "sell"
            order = self.executor.place_oco_order(
                symbol=pair, side=side, quantity=qty,
                price=entry, stop_price=sl, limit_price=tp,
            )
            if order:
                logger.info(f"[AutoTrader] {direction} {pair}: qty={qty:.4f} @ ${entry:.4f} (${notional:.2f})")
                self._trade_history.append({
                    "symbol": pair, "side": direction,
                    "entry": entry, "qty": qty, "sl": sl, "tp": tp,
                    "notional": notional, "fee": fee_cost,
                    "time": __import__('datetime').datetime.now().isoformat(),
                })

        # Update equity
        try:
            balance = self.executor.get_balance()
            if balance is not None:
                old_peak = self._equity.get("peak_balance", balance)
                self._equity["balance"] = balance
                self._equity["peak_balance"] = max(old_peak, balance)
                peak = self._equity["peak_balance"]
                self._equity["drawdown_pct"] = round((peak - balance) / peak * 100, 2) if peak > 0 else 0
        except Exception:
            pass