"""
Agent Scheduler — Cron orchestration for autonomous cycles.
Manages screening, management, and reflection loops on independent schedules.
"""
import asyncio
import logging
import time
from typing import Callable, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class AgentScheduler:
    """Manages periodic agent cycles with independent schedules."""

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._last_run: Dict[str, datetime] = {}
        self._cycle_count: Dict[str, int] = {}

    async def start(self, screening_fn: Callable, management_fn: Callable,
                    reflection_fn: Callable, health_fn: Callable,
                    screening_interval: int = 900,    # 15 min
                    management_interval: int = 300,    # 5 min
                    reflection_interval: int = 3600,   # 60 min
                    health_interval: int = 60):        # 1 min
        """Start all agent cycles."""
        self._running = True
        logger.info("[Scheduler] Starting autonomous cycles...")

        # Launch independent loops
        self._tasks["screening"] = asyncio.create_task(
            self._run_loop("screening", screening_fn, screening_interval))
        self._tasks["management"] = asyncio.create_task(
            self._run_loop("management", management_fn, management_interval))
        self._tasks["reflection"] = asyncio.create_task(
            self._run_loop("reflection", reflection_fn, reflection_interval))
        self._tasks["health"] = asyncio.create_task(
            self._run_loop("health", health_fn, health_interval))

        logger.info(f"[Scheduler] Screening: every {screening_interval}s")
        logger.info(f"[Scheduler] Management: every {management_interval}s")
        logger.info(f"[Scheduler] Reflection: every {reflection_interval}s")
        logger.info(f"[Scheduler] Health: every {health_interval}s")

    async def stop(self):
        """Stop all agent cycles gracefully."""
        self._running = False
        logger.info("[Scheduler] Stopping all cycles...")

        for name, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()
        logger.info("[Scheduler] All cycles stopped")

    async def _run_loop(self, name: str, fn: Callable, interval: int):
        """Run a function periodically with error recovery."""
        logger.info(f"[Scheduler] {name} loop started")
        while self._running:
            try:
                start = time.time()
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
                elapsed = time.time() - start
                self._last_run[name] = datetime.now()
                self._cycle_count[name] = self._cycle_count.get(name, 0) + 1

                # Sleep for remaining interval
                sleep_time = max(0, interval - elapsed)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Scheduler] {name} error: {e}")
                # Backoff on error: 10s, 30s, 60s, 5min
                backoff = min(300, 10 * (2 ** min(self._cycle_count.get(name, 0), 4)))
                await asyncio.sleep(backoff)

    def get_status(self) -> Dict:
        """Return scheduler status."""
        return {
            "running": self._running,
            "cycles": dict(self._cycle_count),
            "last_run": {k: v.isoformat() if v else None for k, v in self._last_run.items()},
            "active_tasks": len(self._tasks),
        }

    def is_running(self) -> bool:
        return self._running
