"""
Risk Manager — overfitting detection, adaptive risk tiers, Kelly-based position sizing.
Circuit breaker now properly increments loss counters.
"""
import logging
import json
from typing import Dict
from pathlib import Path

from src.position_sizing import get_risk_tier, calculate_adaptive_position_size

logger = logging.getLogger(__name__)


class RiskConfig:
    def __init__(self, config: dict = None):
        config = config or {}
        self.max_position_size = config.get("max_position_size", 0.05)
        self.max_drawdown = config.get("max_drawdown", 0.20)
        self.overfit_wr_variance_threshold = config.get("overfit_wr_variance_threshold", 0.15)
        self.min_samples_for_overfit_check = config.get("min_samples_for_overfit_check", 100)
        self.max_consecutive_losses = config.get("max_consecutive_losses", 8)
        self.target_capital = config.get("target_capital", 10000.0)


class RiskManager:
    def __init__(self, config=None, db_path=None):
        self.config = config or RiskConfig()
        self.db_path = db_path
        self.status = {
            "overfitting_detected": False, "samples_available": 0,
            "current_drawdown": 0.0, "consecutive_losses": 0,
        }
        self.current_drawdown = 0.0
        self.peak_equity = 0.0
        self.current_equity = 0.0
        self._status_path = Path("data/overfit_status.json")
        self._load_status()

    def _load_status(self):
        if self._status_path.exists():
            try:
                with open(self._status_path) as f:
                    saved = json.load(f)
                self.status.update(saved)
                self.peak_equity = saved.get("peak_equity", 0.0)
                self.current_equity = saved.get("current_equity", 0.0)
            except Exception:
                pass

    def _save_status(self):
        try:
            self._status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status["peak_equity"] = self.peak_equity
            self.status["current_equity"] = self.current_equity
            with open(self._status_path, "w") as f:
                json.dump(self.status, f, indent=2)
        except Exception as e:
            logger.warning(f"[RiskManager] Save error: {e}")

    def record_trade_result(self, is_win: bool):
        """Called after each trade outcome to update loss streak."""
        if is_win:
            self.status["consecutive_losses"] = 0
        else:
            self.status["consecutive_losses"] = self.status.get("consecutive_losses", 0) + 1
        self._save_status()

    def can_trade(self, signal: Dict) -> Dict:
        """Check if trade is allowed. Paper mode (equity=0) always allows."""
        if self.current_equity <= 0:
            return {"allowed": True, "reason": "paper_mode"}

        if self.status.get("overfitting_detected"):
            return {"allowed": False, "reason": "Overfitting detected via walk-forward variance"}

        tier = get_risk_tier(self.current_equity, self.config.target_capital)
        if self.current_drawdown >= tier.max_dd:
            return {"allowed": False,
                    "reason": f"Max DD {self.current_drawdown:.1%} >= {tier.max_dd:.0%}"}

        if self.status.get("consecutive_losses", 0) >= self.config.max_consecutive_losses:
            return {"allowed": False,
                    "reason": f"Circuit breaker: {self.status['consecutive_losses']} consecutive losses"}

        result = {"allowed": True, "tier": tier.label, "tier_max_leverage": tier.max_leverage}
        result["position_sizing"] = self.get_position_sizing(signal)
        return result

    def get_position_sizing(self, signal: Dict) -> Dict:
        capital = self.current_equity or 100.0
        entry = float(signal.get("entry", 0) or signal.get("price", 0))
        sl = float(signal.get("sl", 0))
        win_rate = float(signal.get("ml_confidence", 55)) / 100.0
        rr_ratio = self._estimate_rr_ratio(signal)
        pair = signal.get("symbol", "")
        if entry <= 0 or sl <= 0:
            return {"size_pct": 0, "reason": "missing entry/sl"}
        return calculate_adaptive_position_size(
            capital=capital, target_capital=self.config.target_capital,
            entry_price=entry, stop_loss_price=sl, win_rate=win_rate,
            rr_ratio=rr_ratio, pair=pair, use_maker=True)

    def _estimate_rr_ratio(self, signal: Dict) -> float:
        entry = float(signal.get("entry", 0) or signal.get("price", 0))
        sl = float(signal.get("sl", 0))
        tp = float(signal.get("tp", 0))
        if entry > 0 and sl > 0 and tp > 0:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0: return round(reward / risk, 2)
        sl_mult = float(signal.get("atr_sl_mult", 1.5))
        tp_mult = float(signal.get("atr_tp_mult", 3.0))
        return round(tp_mult / sl_mult, 2) if sl_mult > 0 else 2.0

    def update_equity(self, current_equity: float):
        self.current_equity = current_equity
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        if self.peak_equity > 0:
            self.current_drawdown = (self.peak_equity - current_equity) / self.peak_equity
            self.status["current_drawdown"] = round(self.current_drawdown, 4)
        self._save_status()

    def update_drawdown(self, current_equity: float, peak_equity: float):
        self.current_equity = current_equity
        self.peak_equity = peak_equity
        if peak_equity > 0:
            self.current_drawdown = (peak_equity - current_equity) / peak_equity
            self.status["current_drawdown"] = round(self.current_drawdown, 4)
        self._save_status()

    def check_overfit_walkforward(self, fold_win_rates: list, samples: int) -> bool:
        """Check overfitting using actual fold WR variance."""
        self.status["samples_available"] = samples
        if samples < self.config.min_samples_for_overfit_check or len(fold_win_rates) < 3:
            self.status["overfitting_detected"] = False
            self._save_status()
            return False
        wr_variance = max(fold_win_rates) - min(fold_win_rates)
        if wr_variance > self.config.overfit_wr_variance_threshold:
            self.status["overfitting_detected"] = True
            self.status["overfit_wr_variance"] = round(wr_variance, 4)
            logger.warning(f"[RiskManager] Overfit: WR variance={wr_variance:.3f}")
        else:
            self.status["overfitting_detected"] = False
        self._save_status()
        return self.status["overfitting_detected"]

    def reset_overfit_flag(self):
        self.status["overfitting_detected"] = False
        self.status["samples_available"] = 0
        self.status["consecutive_losses"] = 0
        self.status.pop("overfit_wr_variance", None)
        self._save_status()
        logger.info("[RiskManager] Flags reset")

    def get_risk_tier_info(self) -> Dict:
        capital = self.current_equity or 100.0
        tier = get_risk_tier(capital, self.config.target_capital)
        return {
            "current_equity": round(capital, 2),
            "target_capital": self.config.target_capital,
            "progress_pct": round(min(capital / self.config.target_capital * 100, 100), 1),
            "tier": tier.label, "risk_per_trade": f"{tier.risk_pct:.1%}",
            "max_leverage": tier.max_leverage, "max_drawdown": f"{tier.max_dd:.0%}",
            "current_drawdown": f"{self.current_drawdown:.1%}",
            "consecutive_losses": self.status.get("consecutive_losses", 0),
        }

    def get_status(self) -> Dict:
        status = self.status.copy()
        status["risk_tier"] = self.get_risk_tier_info()
        return status


def get_risk_manager(config=None, db_path=None) -> RiskManager:
    risk_config = None
    if config and hasattr(config, 'max_position_size'):
        risk_config = RiskConfig({
            "max_position_size": config.max_position_size,
            "max_drawdown": config.max_drawdown,
            "target_capital": getattr(config, 'target_capital', 10000.0),
        })
    return RiskManager(risk_config, db_path)
