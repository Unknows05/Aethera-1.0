"""
Agent Manager — autonomous position monitoring.
Rules first (SL/TP/timeout) → LLM for ambiguous (trail/regime-flip/partial).
"""
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


class AgentManager:
    def __init__(self, engine):
        self.engine = engine
        self._busy = False

    def run_cycle(self, prices: Dict[str, float]) -> List[Dict]:
        """Check all open positions, return management actions."""
        if self._busy:
            return []
        self._busy = True
        actions = []

        try:
            open_symbols = self.engine.db.get_open_signal_symbols()
            c = self.engine.db.conn.cursor()
            current_regime = self.engine._get_current_market_regime()

            for sym in open_symbols:
                price = prices.get(sym)
                if not price:
                    continue

                c.execute(
                    "SELECT * FROM signals WHERE symbol=? AND result IN ('OPEN','PENDING') ORDER BY id DESC LIMIT 1",
                    (sym,))
                row = c.fetchone()
                if not row:
                    continue

                signal = dict(row)
                direction = signal.get("signal", "")
                entry = signal.get("entry_price", 0)
                sl = signal.get("sl", 0)
                tp = signal.get("tp", 0)
                regime_at_entry = signal.get("regime", "")
                ts = signal.get("timestamp", "")

                if entry <= 0 or sl <= 0:
                    continue

                # ── PnL calculation ──
                if direction == "LONG":
                    pnl_pct = (price - entry) / entry * 100
                else:
                    pnl_pct = (entry - price) / entry * 100

                # Hours held
                try:
                    held_hours = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
                except:
                    held_hours = 0

                # ── STEP 1: Deterministic rules (NO LLM) ──
                action = None

                # SL/TP check — already done by DB outcome checker, but double-check
                if direction == "LONG" and price <= sl:
                    action = {"action": "CLOSE", "reason": "SL hit"}
                elif direction == "SHORT" and price >= sl:
                    action = {"action": "CLOSE", "reason": "SL hit"}
                elif direction == "LONG" and price >= tp:
                    action = {"action": "CLOSE", "reason": "TP hit"}
                elif direction == "SHORT" and price <= tp:
                    action = {"action": "CLOSE", "reason": "TP hit"}
                elif held_hours >= 24:
                    action = {"action": "CLOSE", "reason": "24h timeout"}

                if action:
                    actions.append({**action, "symbol": sym, "price": price,
                                    "pnl_pct": round(pnl_pct, 2), "source": "RULE"})
                    continue

                # ── STEP 2: Ambiguous → LLM decide ──
                # Regime flip check
                if current_regime != regime_at_entry:
                    action = self._llm_manage(
                        signal, price, pnl_pct, held_hours, current_regime)
                    if action:
                        actions.append({**action, "symbol": sym, "price": price,
                                        "pnl_pct": round(pnl_pct, 2), "source": "LLM"})
        finally:
            self._busy = False

        return actions

    def _llm_manage(self, signal: Dict, current_price: float,
                    pnl_pct: float, held_hours: float, current_regime: str) -> Dict:
        """Use LLM for ambiguous management decisions."""
        try:
            from src.llm_brain import get_llm_brain
            from src.lessons import get_lessons_summary
            from src.coin_memory import get_coin_note_for_prompt

            llm = get_llm_brain()
            if not llm.is_ready():
                return {"action": "STAY", "reason": "LLM not available (no API key)"}

            symbol = signal.get("symbol", "")
            entry = signal.get("entry_price", 0)
            direction = signal.get("signal", "")
            sl = signal.get("sl", 0)
            tp = signal.get("tp", 0)
            regime_entry = signal.get("regime", "")

            goal = (f"Position: {direction} {symbol} @ ${entry:.4f}, current: ${current_price:.4f} "
                    f"| PnL: {pnl_pct:+.2f}% | Held: {held_hours:.1f}h "
                    f"| SL: ${sl:.4f} | TP: ${tp:.4f} | "
                    f"Regime now: {current_regime} (was: {regime_entry}). "
                    f"Action: STAY / TRAIL_SL(price) / CLOSE / PARTIAL_TP? Reply in JSON.")

            context = {
                "lessons": get_lessons_summary(),
                "coin_memory": get_coin_note_for_prompt(symbol),
            }

            return llm.decide_sync(goal, "MANAGER", context)
        except Exception as e:
            logger.error(f"[Manager] LLM error: {e}")
            return {"action": "STAY", "reason": f"LLM error: {e}"}
