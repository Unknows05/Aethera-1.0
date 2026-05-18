"""
Daily Goal — tracks daily profit target progress and triggers LLM re-evaluation.

Triggers: after 2 trades, 70% target hit, or -50% loss.
Persists state to data/daily_state.json.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict

logger = logging.getLogger(__name__)

STATE_PATH = "data/daily_state.json"


class DailyGoal:
    def __init__(self, target_pct: float = 0.05, capital_start: float = 0):
        self.target_pct = target_pct
        self.capital_start = capital_start
        self.trades_taken = 0
        self.current_pnl: float = 0.0
        self._last_reset_day: str = ""
        self._load()

    @property
    def target_amount(self) -> float:
        return self.capital_start * self.target_pct

    @property
    def target_usd(self) -> float:
        return self.target_amount

    def update(self, pnl: float):
        self._check_day_reset()
        self.current_pnl += pnl
        self.trades_taken += 1
        self._save()
        logger.info(
            f"[DailyGoal] Trade #{self.trades_taken} | PnL: ${pnl:+.2f} | "
            f"Cumulative: ${self.current_pnl:+.2f} | Progress: {self._progress_pct():.1f}%"
        )

    def should_re_evaluate(self) -> bool:
        progress = self._progress_pct()
        if self.trades_taken >= 2:
            return True
        if progress >= 70:
            return True
        if progress <= -50:
            return True
        return False

    def is_complete(self) -> bool:
        progress = self._progress_pct()
        if progress >= 100:
            logger.info(f"[DailyGoal] Target reached: {progress:.1f}%")
            return True
        if progress <= -100:
            logger.warning(f"[DailyGoal] Drawdown limit hit: {progress:.1f}%")
            return True
        return False

    def get_progress(self) -> Dict:
        return {
            "target_pct": round(self.target_pct * 100, 2),
            "capital_start": self.capital_start,
            "target_amount": round(self.target_amount, 2),
            "trades_taken": self.trades_taken,
            "current_pnl": round(self.current_pnl, 2),
            "progress_pct": round(self._progress_pct(), 2),
            "is_complete": self.is_complete(),
            "should_re_evaluate": self.should_re_evaluate(),
        }

    def reset_day(self, capital: float):
        self.capital_start = capital
        self.trades_taken = 0
        self.current_pnl = 0.0
        self._last_reset_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._save()
        logger.info(f"[DailyGoal] Reset for new day. Capital: ${capital:,.2f}")

    def adjust_target(self, new_pct: float):
        self.target_pct = max(0.005, min(new_pct, 0.20))
        self._save()
        logger.info(f"[DailyGoal] Target adjusted to {self.target_pct * 100:.1f}%")

    def _progress_pct(self) -> float:
        if self.target_amount <= 0 or self.capital_start <= 0:
            return 0.0
        return self.current_pnl / self.target_amount * 100

    def _check_day_reset(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_reset_day and self._last_reset_day != today:
            logger.info(f"[DailyGoal] New day detected ({today}), resetting")
            self.trades_taken = 0
            self.current_pnl = 0.0
            self._last_reset_day = today

    def _save(self):
        os.makedirs("data", exist_ok=True)
        data = {
            "target_pct": self.target_pct,
            "capital_start": self.capital_start,
            "trades_taken": self.trades_taken,
            "current_pnl": self.current_pnl,
            "last_reset_day": self._last_reset_day,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(STATE_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self):
        if not os.path.exists(STATE_PATH):
            self._save()
            return
        try:
            with open(STATE_PATH) as f:
                data = json.load(f)
            saved_day = data.get("last_reset_day", "")
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if saved_day != today:
                logger.info(f"[DailyGoal] Stale state from {saved_day}, starting fresh")
                self._last_reset_day = today
                self._save()
                return
            self.target_pct = data.get("target_pct", self.target_pct)
            self.capital_start = data.get("capital_start", self.capital_start)
            self.trades_taken = data.get("trades_taken", 0)
            self.current_pnl = data.get("current_pnl", 0.0)
            self._last_reset_day = data.get("last_reset_day", today)
            logger.info(f"[DailyGoal] Loaded: {self.trades_taken} trades, ${self.current_pnl:+.2f} PnL")
        except Exception as e:
            logger.error(f"[DailyGoal] Failed to load state: {e}")

    def save(self):
        self._save()

    def load(self):
        self._load()
        return self.get_progress()
