"""
Trailing Take Profit — activates when PnL exceeds trigger threshold,
then trails the peak and closes when drawdown from peak exceeds drop threshold.
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class TrailingTP:
    def __init__(self, trigger_pct: float = 3.0, drop_pct: float = 1.5):
        self.trigger_pct = trigger_pct
        self.drop_pct = drop_pct
        self._positions: Dict[str, Dict] = {}

    def check(self, symbol: str, current_pnl_pct: float) -> str:
        state = self._positions.get(symbol)

        if state is None:
            if current_pnl_pct >= self.trigger_pct:
                self._positions[symbol] = {
                    "active": True,
                    "peak_pnl": current_pnl_pct,
                    "entry_pnl": current_pnl_pct,
                }
                logger.info(
                    f"[TrailingTP] {symbol} ACTIVATED at {current_pnl_pct:.2f}% "
                    f"(trigger={self.trigger_pct}%)"
                )
                return "ACTIVATE"
            return "HOLD"

        if not state["active"]:
            return "HOLD"

        state["peak_pnl"] = max(state["peak_pnl"], current_pnl_pct)
        drawdown_from_peak = state["peak_pnl"] - current_pnl_pct

        if current_pnl_pct <= 0:
            logger.info(f"[TrailingTP] {symbol} closed below breakeven")
            self._reset(symbol)
            return "CLOSE_TRAILING_TP"

        if drawdown_from_peak >= self.drop_pct:
            logger.info(
                f"[TrailingTP] {symbol} TRAILING STOP TRIGGERED: "
                f"peak={state['peak_pnl']:.2f}%, current={current_pnl_pct:.2f}%, "
                f"drawdown={drawdown_from_peak:.2f}% >= drop={self.drop_pct}%"
            )
            self._reset(symbol)
            return "CLOSE_TRAILING_TP"

        return "HOLD"

    def get_state(self, symbol: str) -> Optional[Dict]:
        return self._positions.get(symbol)

    def is_active(self, symbol: str) -> bool:
        state = self._positions.get(symbol)
        return state is not None and state.get("active", False)

    def get_peak_pnl(self, symbol: str) -> float:
        state = self._positions.get(symbol)
        return state.get("peak_pnl", 0.0) if state else 0.0

    def reset(self, symbol: str):
        self._reset(symbol)

    def reset_all(self):
        self._positions.clear()
        logger.info("[TrailingTP] All positions reset")

    def _reset(self, symbol: str):
        self._positions.pop(symbol, None)
