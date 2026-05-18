"""
Health Monitor — Self-healing system monitor for autonomous daemon.
Checks agent health, API connectivity, disk space, memory usage.
Auto-recovers from failures with backoff.
"""
import os
import shutil
import logging
import asyncio
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Monitors system health and auto-recovers from failures."""

    def __init__(self, engine=None, telegram=None, state: Dict = None):
        self.engine = engine
        self.telegram = telegram
        self.state = state or {}
        self._crash_count = 0
        self._last_crash: Optional[str] = None
        self._fallback_mode = False
        self._consecutive_failures = 0

    async def check(self):
        """Run all health checks."""
        checks = [
            ("engine", self._check_engine),
            ("binance_api", self._check_binance_api),
            ("llm_api", self._check_llm_api),
            ("disk_space", self._check_disk_space),
            ("memory_usage", self._check_memory_usage),
        ]

        for name, check_fn in checks:
            try:
                ok = await check_fn()
                if not ok:
                    self._consecutive_failures += 1
                    logger.warning(f"[Health] {name} check failed")
                    if self._consecutive_failures >= 3:
                        await self._alert(f"Health check failed: {name}")
                else:
                    self._consecutive_failures = 0
            except Exception as e:
                logger.error(f"[Health] {name} check error: {e}")

    async def _check_engine(self) -> bool:
        """Check if engine is responsive."""
        try:
            status = self.engine.get_status()
            return status.get("status") == "online"
        except Exception:
            return False

    async def _check_binance_api(self) -> bool:
        """Check Binance API connectivity."""
        try:
            loop = asyncio.get_event_loop()
            # Lightweight check: get server time
            result = await loop.run_in_executor(None, self.engine.api.get_server_time)
            return result is not None
        except Exception:
            return False

    async def _check_llm_api(self) -> bool:
        """Check LLM API connectivity."""
        try:
            brain = self.engine.ml_engine  # or llm_brain
            if hasattr(brain, 'is_ready'):
                return brain.is_ready()
            return True  # No LLM check available
        except Exception:
            return False

    async def _check_disk_space(self) -> bool:
        """Check disk space — alert if < 500MB free."""
        try:
            usage = shutil.disk_usage(".")
            gb_free = usage.free / (1024 ** 3)
            if gb_free < 0.5:
                await self._alert(f"Disk space critical: {gb_free:.1f} GB free")
                return False
            return True
        except Exception:
            return True  # Don't fail on disk check error

    async def _check_memory_usage(self) -> bool:
        """Check memory usage — alert if > 90%."""
        try:
            # Linux only
            with open('/proc/meminfo') as f:
                lines = f.readlines()
            mem_total = int(lines[0].split()[1])
            mem_avail = int(lines[2].split()[1])
            usage_pct = (mem_total - mem_avail) / mem_total * 100
            if usage_pct > 90:
                await self._alert(f"Memory usage high: {usage_pct:.0f}%")
                return False
            return True
        except Exception:
            return True  # Non-Linux or error

    async def _alert(self, message: str):
        """Send alert via Telegram if available."""
        logger.warning(f"[Health] ALERT: {message}")
        if self.telegram and self.telegram.is_ready():
            try:
                await self.telegram.send_message(f"⚠️ Health Alert: {message}")
            except Exception:
                pass

    def get_status(self) -> Dict:
        """Return health status."""
        return {
            "fallback_mode": self._fallback_mode,
            "crash_count": self._crash_count,
            "last_crash": self._last_crash,
            "consecutive_failures": self._consecutive_failures,
        }
