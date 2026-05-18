"""
Trade Cooldown — prevents thrashing by blocking symbols after repeated losses.

Limits: max 3 losses in 12-hour window triggers 4-hour cooldown.
Reads trade history from src.coin_memory.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from src.coin_memory import recall_coin

logger = logging.getLogger(__name__)


class TradeCooldown:
    def __init__(self, max_losses: int = 3, window_hours: int = 12,
                 cooldown_hours: int = 4):
        self.max_losses = max_losses
        self.window_hours = window_hours
        self.cooldown_hours = cooldown_hours
        self._blocked: Dict[str, float] = {}

    def should_block(self, symbol: str) -> bool:
        coin_data = recall_coin(symbol)
        trades: List[Dict] = coin_data.get("trades", []) if isinstance(coin_data, dict) else []

        if not trades:
            return False

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self.window_hours)
        recent_losses = 0

        for t in reversed(trades):
            try:
                ts = datetime.fromisoformat(str(t.get("timestamp", "")))
            except (ValueError, TypeError):
                continue
            if ts >= cutoff:
                if t.get("result") == "LOSS":
                    recent_losses += 1
            else:
                break

        if recent_losses >= self.max_losses:
            unblock_at = now + timedelta(hours=self.cooldown_hours)
            self._blocked[symbol] = unblock_at.timestamp()
            logger.warning(
                f"[Cooldown] {symbol} blocked: {recent_losses} losses in "
                f"{self.window_hours}h window. Until: {unblock_at.strftime('%H:%M UTC')}"
            )
            # ── Audit Chain: log cooldown block ──
            try:
                from src.audit_chain import get_audit_chain
                chain = get_audit_chain()
                chain.append({
                    "type": "cooldown_block",
                    "symbol": symbol,
                    "recent_losses": recent_losses,
                    "window_hours": self.window_hours,
                    "cooldown_hours": self.cooldown_hours,
                    "until": unblock_at.isoformat(),
                })
            except Exception:
                pass
            return True

        # Check existing block
        if symbol in self._blocked:
            block_until_ts = self._blocked[symbol]
            if now.timestamp() < block_until_ts:
                block_time = datetime.fromtimestamp(block_until_ts, tz=timezone.utc)
                logger.debug(f"[Cooldown] {symbol} still blocked until {block_time.strftime('%H:%M UTC')}")
                return True
            self._blocked.pop(symbol, None)
            logger.info(f"[Cooldown] {symbol} cooldown expired, unblocking")

        return False

    def block_until(self, symbol: str) -> float:
        return self._blocked.get(symbol, 0.0)

    def reset(self, symbol: str):
        self._blocked.pop(symbol, None)
        logger.info(f"[Cooldown] {symbol} manually reset")

    def reset_all(self):
        self._blocked.clear()
        logger.info("[Cooldown] All cooldowns reset")

    def get_blocked_symbols(self) -> List[str]:
        now = datetime.now(timezone.utc).timestamp()
        active = [s for s, ts in self._blocked.items() if now < ts]
        expired = [s for s, ts in self._blocked.items() if now >= ts]
        for s in expired:
            self._blocked.pop(s, None)
        return active
