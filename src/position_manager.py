"""
Position Manager — trail stop, regime-aware early close, multi-tier management.
Runs after each scan, evaluates open positions for STAY/TRAIL/CLOSE decisions.
"""
import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PositionManager:
    """Monitors open positions and suggests management actions."""

    def __init__(self, db, risk_manager=None):
        self.db = db
        self.risk_manager = risk_manager

    def evaluate_position(self, signal: Dict, current_price: float,
                           current_regime: str) -> Dict:
        """
        Evaluate an open position and return management action.
        Returns: {action: STAY|TRAIL_SL|CLOSE, reason: str, new_sl: float|None}
        """
        entry = signal.get("entry_price", 0)
        sl = signal.get("sl", 0)
        tp = signal.get("tp", 0)
        direction = signal.get("signal", "")
        regime_at_entry = signal.get("regime", "")

        if entry <= 0 or sl <= 0:
            return {"action": "STAY", "reason": "invalid data"}

        # PnL since entry
        if direction == "LONG":
            pnl_pct = (current_price - entry) / entry * 100
            profit_targets = [
                (1.0, "1R reached — trail SL to breakeven"),
                (2.0, "2R reached — trail SL to +0.5R"),
                (3.0, "3R reached — trail SL to +1R"),
            ]
        else:  # SHORT
            pnl_pct = (entry - current_price) / entry * 100
            profit_targets = [
                (1.0, "1R reached — trail SL to breakeven"),
                (2.0, "2R reached — trail SL to +0.5R"),
                (3.0, "3R reached — trail SL to +1R"),
            ]

        # ── Regime Flip Check ──
        if current_regime != regime_at_entry:
            if regime_at_entry == "BULL" and current_regime in ("BEAR", "SIDEWAYS"):
                if direction == "LONG":
                    return {"action": "CLOSE", "reason": f"Regime flipped {regime_at_entry}→{current_regime} (against LONG)", "new_sl": None}
            if regime_at_entry == "BEAR" and current_regime in ("BULL", "SIDEWAYS"):
                if direction == "SHORT":
                    return {"action": "CLOSE", "reason": f"Regime flipped {regime_at_entry}→{current_regime} (against SHORT)", "new_sl": None}

        # ── Trail Stop Logic ──
        sl_distance = abs(entry - sl) / entry * 100  # SL distance in %
        atr_mult = signal.get("atr_sl_mult", 1.5)

        for r_mult, reason in profit_targets:
            if pnl_pct >= sl_distance * r_mult:
                if r_mult == 1.0:
                    new_sl = entry
                    return {"action": "TRAIL_SL", "reason": reason, "new_sl": new_sl}
                elif r_mult == 2.0:
                    new_sl = entry + (entry * sl_distance * 0.5 / 100) if direction == "LONG" else entry - (entry * sl_distance * 0.5 / 100)
                    return {"action": "TRAIL_SL", "reason": reason, "new_sl": new_sl}
                else:
                    new_sl = entry + (entry * sl_distance * 1.0 / 100) if direction == "LONG" else entry - (entry * sl_distance * 1.0 / 100)
                    return {"action": "TRAIL_SL", "reason": reason, "new_sl": new_sl}

        return {"action": "STAY", "reason": "no trigger", "new_sl": None}

    def manage_all(self, prices: Dict[str, float], current_regime: str) -> list:
        """Evaluate all open positions, return management actions."""
        open_symbols = self.db.get_open_signal_symbols()
        actions = []
        c = self.db.conn.cursor()
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
            action = self.evaluate_position(signal, price, current_regime)
            if action["action"] != "STAY":
                actions.append({**action, "symbol": sym, "price": price,
                                "signal_id": signal.get("id")})
                logger.info(f"[PositionMgr] {sym}: {action['action']} — {action['reason']}")
        return actions
