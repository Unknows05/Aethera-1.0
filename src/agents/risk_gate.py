"""
Risk Gate — Hard rules (code-enforced) + Soft rules (LLM can override).
Deterministic safety layer that no agent can bypass.
"""
import logging
import json
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class RiskGate:
    """Enforces risk rules with hard gates and LLM-overridable soft rules."""

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self._overrides: List[Dict] = []
        self._override_count_today = 0
        self._last_override_date = datetime.now().strftime("%Y-%m-%d")

    def check(self, signal: Dict, portfolio: Dict, regime: str = "UNKNOWN") -> Dict:
        """Run full risk check. Returns {allowed, reason, overrides}."""
        result = {"allowed": True, "reason": "", "overrides": [], "risk_level": "low"}

        # Hard Rules (NO LLM override)
        hard_result = self._check_hard_rules(signal, portfolio)
        if not hard_result["allowed"]:
            return hard_result

        # Soft Rules (LLM can override)
        soft_result = self._check_soft_rules(signal, portfolio, regime)
        if not soft_result["allowed"]:
            result["allowed"] = False
            result["reason"] = soft_result["reason"]
            result["risk_level"] = soft_result.get("risk_level", "medium")
            result["soft_blocked"] = True
            result["override_available"] = True

        return result

    def _check_hard_rules(self, signal: Dict, portfolio: Dict) -> Dict:
        """Hard rules — cannot be overridden."""
        # Rule 1: Max drawdown
        dd = portfolio.get("drawdown_pct", 0)
        max_dd = self.config.get("max_drawdown_pct", 20)
        if dd >= max_dd:
            return {
                "allowed": False,
                "reason": f"Drawdown {dd:.1f}% >= max {max_dd}% — CIRCUIT BREAKER",
                "risk_level": "critical",
                "rule": "max_drawdown",
            }

        # Rule 2: Circuit breaker (3 consecutive losses)
        loss_streak = portfolio.get("loss_streak", 0)
        max_streak = self.config.get("circuit_breaker_losses", 3)
        if loss_streak >= max_streak:
            pause_until = portfolio.get("circuit_breaker_resume")
            if pause_until and datetime.now().isoformat() < pause_until:
                return {
                    "allowed": False,
                    "reason": f"Circuit breaker: {loss_streak} loss streak — paused until {pause_until}",
                    "risk_level": "critical",
                    "rule": "circuit_breaker",
                }

        # Rule 3: Blacklisted symbols
        symbol = signal.get("symbol", "")
        blacklist = self.config.get("blacklist", [])
        if symbol in blacklist:
            return {
                "allowed": False,
                "reason": f"Symbol {symbol} is blacklisted",
                "risk_level": "high",
                "rule": "blacklist",
            }

        # Rule 4: Max daily trades
        daily_trades = portfolio.get("daily_trades", 0)
        max_trades = self.config.get("max_trades_per_day", 5)
        if daily_trades >= max_trades:
            return {
                "allowed": False,
                "reason": f"Daily trade limit reached: {daily_trades}/{max_trades}",
                "risk_level": "medium",
                "rule": "max_daily_trades",
            }

        return {"allowed": True, "reason": "", "risk_level": "low"}

    def _check_soft_rules(self, signal: Dict, portfolio: Dict, regime: str) -> Dict:
        """Soft rules — LLM can override with written reason."""
        issues = []

        # Soft Rule 1: Regime conflict
        signal_direction = signal.get("signal", "")
        if signal_direction == "LONG" and regime == "BEAR":
            issues.append(f"LONG signal in BEAR regime")
        elif signal_direction == "SHORT" and regime == "BULL":
            issues.append(f"SHORT signal in BULL regime")

        # Soft Rule 2: High funding rate
        funding = signal.get("funding_rate", 0)
        if abs(funding) > 0.05:
            issues.append(f"High funding rate: {funding:.4f}%")

        # Soft Rule 3: Low confidence
        confidence = signal.get("confidence", 0)
        min_conf = self.config.get("min_confidence", 55)
        if confidence < min_conf:
            issues.append(f"Confidence {confidence}% below minimum {min_conf}%")

        # Soft Rule 4: Correlation exposure
        correlation = portfolio.get("correlation_exposure", 0)
        max_corr = self.config.get("max_correlation_exposure", 0.3)
        if correlation > max_corr:
            issues.append(f"Correlation exposure {correlation:.2f} > max {max_corr}")

        if issues:
            return {
                "allowed": False,
                "reason": "; ".join(issues),
                "risk_level": "medium",
                "issues": issues,
            }

        return {"allowed": True, "reason": "", "risk_level": "low"}

    def apply_override(self, signal: Dict, reason: str, confidence: int) -> Dict:
        """Apply LLM override for soft rules. Returns override record."""
        # Reset daily counter if new day
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._last_override_date:
            self._override_count_today = 0
            self._last_override_date = today

        # Check max overrides per day
        max_overrides = self.config.get("max_overrides_per_day", 3)
        if self._override_count_today >= max_overrides:
            return {
                "allowed": False,
                "reason": f"Max overrides reached today: {self._override_count_today}/{max_overrides}",
            }

        # Check minimum override confidence
        min_override_conf = self.config.get("llm_override_confidence_min", 70)
        if confidence < min_override_conf:
            return {
                "allowed": False,
                "reason": f"Override confidence {confidence}% < minimum {min_override_conf}%",
            }

        # Record override
        override = {
            "timestamp": datetime.now().isoformat(),
            "symbol": signal.get("symbol", ""),
            "signal": signal.get("signal", ""),
            "reason": reason,
            "confidence": confidence,
        }
        self._overrides.append(override)
        self._override_count_today += 1

        logger.info(f"[RiskGate] Override applied: {signal.get('symbol')} — {reason}")
        return {"allowed": True, "override": override}

    def get_override_history(self, limit: int = 10) -> List[Dict]:
        """Return recent override history."""
        return self._overrides[-limit:]

    def get_status(self) -> Dict:
        """Return risk gate status."""
        return {
            "overrides_today": self._override_count_today,
            "total_overrides": len(self._overrides),
            "last_override": self._overrides[-1] if self._overrides else None,
        }
