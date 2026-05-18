"""
Telegram Bot — Sends screening signals and WR updates via Telegram.

Integrates with the existing display.py formatter for consistent formatting.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars or config values.
"""
import logging
import os
import asyncio
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from telegram import Bot
    from telegram.constants import ParseMode
    from telegram.error import TelegramError
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False


class TelegramBot:
    """Sends screening signals and performance updates via Telegram."""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.bot: Optional[Bot] = None
        self.chat_id: Optional[str] = None
        self._initialized = False
        self._connect()

    def _connect(self):
        """Initialize Telegram bot connection."""
        if not HAS_TELEGRAM:
            logger.warning("[Telegram] python-telegram-bot not installed")
            return

        token = self.config.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = self.config.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")

        if not token:
            logger.warning("[Telegram] Bot token not configured — disabled")
            return

        try:
            self.bot = Bot(token=token)
            if self.chat_id:
                self._initialized = True
                logger.info(f"[Telegram] Bot initialized for chat {self.chat_id}")
            else:
                logger.info(f"[Telegram] Bot token set — waiting for /start to register chat ID")
                # Start polling in background to catch /start
                self._start_polling()
        except Exception as e:
            logger.error(f"[Telegram] Failed to initialize: {e}")

    def _start_polling(self):
        """Background thread: listen for /start to auto-save chat_id."""
        import threading
        def _poll():
            import requests, time, json
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            if not token: return
            offset = 0
            while not self._initialized:
                try:
                    url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=30"
                    r = requests.get(url, timeout=35)
                    if r.status_code == 200:
                        updates = r.json().get("result", [])
                        for u in updates:
                            offset = u["update_id"] + 1
                            msg = u.get("message", {})
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            text = msg.get("text", "")
                            if chat_id:
                                self.chat_id = chat_id
                                # Save to .env
                                try:
                                    env_path = ".env"
                                    lines = []
                                    found = False
                                    if os.path.exists(env_path):
                                        for line in open(env_path).read().split("\n"):
                                            if line.startswith("TELEGRAM_CHAT_ID="):
                                                lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
                                                found = True
                                            else:
                                                lines.append(line)
                                    if not found:
                                        lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
                                    open(env_path, "w").write("\n".join(lines))
                                    os.environ["TELEGRAM_CHAT_ID"] = chat_id
                                except Exception:
                                    pass
                                self._initialized = True
                                logger.info(f"[Telegram] Chat ID registered: {chat_id}")
                                # Send welcome
                                try:
                                    self.bot.send_message(chat_id=chat_id, text="Connected to Coin Screener. You will receive signal alerts, daily WR reports, and trade notifications here.")
                                except Exception:
                                    pass
                                return
                except Exception:
                    time.sleep(5)
        t = threading.Thread(target=_poll, daemon=True)
        t.start()

    def is_ready(self) -> bool:
        return self._initialized and self.bot is not None

    async def send_signal_alert(self, signal: Dict):
        """Send a new signal alert in real-time."""
        if not self.is_ready():
            return

        try:
            msg = self._format_signal_alert(signal)
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info(f"[Telegram] Signal alert sent: {signal.get('symbol')}")
        except TelegramError as e:
            logger.error(f"[Telegram] Send error: {e}")

    async def send_scan_summary(self, signals: List[Dict], elapsed: float):
        """Send scan summary with top signals."""
        if not self.is_ready() or not signals:
            return

        try:
            msg = self._format_scan_summary(signals, elapsed)
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("[Telegram] Scan summary sent")
        except TelegramError as e:
            logger.error(f"[Telegram] Summary error: {e}")

    async def send_wr_update(self, wr_data: Dict):
        """Send daily/rolling win rate update."""
        if not self.is_ready():
            return

        try:
            msg = self._format_wr_update(wr_data)
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            logger.info("[Telegram] WR update sent")
        except TelegramError as e:
            logger.error(f"[Telegram] WR update error: {e}")

    async def send_ml_status(self, ml_status: Dict):
        """Send ML training status update."""
        if not self.is_ready():
            return

        try:
            trained = "Trained" if ml_status.get("trained") else "Not Trained"
            msg = (
                f"<b>ML Model Status</b>\n\n"
                f"Status: {ml_status.get('status', 'unknown')}\n"
                f"Trained: {trained}\n"
                f"Threshold: {ml_status.get('threshold', 0):.0%}\n"
                f"Samples: {ml_status.get('samples', 0)}\n"
                f"WR (out-of-sample): {ml_status.get('wr_out_of_sample', 0):.1f}%\n"
                f"Top Features: {ml_status.get('top_features', '-')}\n"
                f"Trained at: {ml_status.get('trained_at', '-')}"
            )
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            logger.error(f"[Telegram] ML status error: {e}")

    def _format_signal_alert(self, s: Dict) -> str:
        """Format a single signal for Telegram."""
        sig = s.get("signal", "WAIT")
        emoji = "🟢" if sig == "LONG" else "🔴" if sig == "SHORT" else "⚪"
        conf = s.get("confidence", 0)
        bars = int(conf / 5)
        bar = "█" * bars + "░" * (20 - bars)
        price = s.get("price", 0)

        lines = [
            f"{emoji} <b>NEW {sig}: {s.get('symbol', '?')}</b>",
            f"Price: ${price:,.{4 if price < 1 else 2}f} | Score: {s.get('composite_score', 0):.0f}",
            f"Confidence: [{bar}] {conf}%",
        ]

        entry = s.get("entry")
        if entry:
            lines.append(f"Entry: ${entry:,.{4 if entry < 1 else 2}f}")

        sl = s.get("sl")
        if sl and entry and entry > 0:
            sl_pct = abs((sl - entry) / entry * 100)
            lines.append(f"SL: ${sl:,.{4 if sl < 1 else 2}f} ({sl_pct:.1f}%)")

        tp = s.get("tp")
        if tp and entry and entry > 0:
            tp_pct = abs((tp - entry) / entry * 100)
            lines.append(f"TP: ${tp:,.{4 if tp < 1 else 2}f} ({tp_pct:.1f}%)")

        ml = s.get("ml_confidence", 0)
        if ml:
            lines.append(f"ML Filter: {ml:.0f}% confidence")

        reasons = s.get("reasons", [])
        if reasons:
            lines.append(f"Reasons: {', '.join(reasons[:3])}")

        return "\n".join(lines)

    def _format_scan_summary(self, signals: List[Dict], elapsed: float) -> str:
        """Format scan summary for Telegram."""
        longs = sum(1 for s in signals if s.get("signal") == "LONG")
        shorts = sum(1 for s in signals if s.get("signal") == "SHORT")
        waits = sum(1 for s in signals if s.get("signal") == "WAIT")

        lines = [
            f"<b>Scan Summary</b> ({elapsed:.1f}s)",
            f"Total: {len(signals)} | LONG: {longs} | SHORT: {shorts} | WAIT: {waits}",
            "",
        ]

        top_signals = [s for s in signals if s.get("signal") in ("LONG", "SHORT")]
        top_signals.sort(key=lambda x: x.get("composite_score", 0), reverse=True)

        if top_signals:
            lines.append("<b>Top Signals:</b>")
            for s in top_signals[:5]:
                emoji = "🟢" if s.get("signal") == "LONG" else "🔴"
                lines.append(
                    f"{emoji} {s.get('symbol')} — "
                    f"Score: {s.get('composite_score', 0):.0f} | "
                    f"Conf: {s.get('confidence', 0)}%"
                )
        else:
            lines.append("No active signals — market conditions neutral.")

        return "\n".join(lines)

    def _format_wr_update(self, wr_data: Dict) -> str:
        """Format win rate update message."""
        daily = wr_data.get("daily", {})
        r7d = wr_data.get("rolling_7d", {})
        r30d = wr_data.get("rolling_30d", {})

        def fmt_wr(v):
            return f"{v*100:.1f}%" if v else "N/A"

        lines = [
            "<b>Win Rate Update</b>",
            "",
            f"Today: {fmt_wr(daily.get('wr'))} ({daily.get('trades', 0)} trades)",
            f"7-Day Rolling: {fmt_wr(r7d.get('wr'))} ({r7d.get('trades', 0)} trades)",
            f"30-Day Rolling: {fmt_wr(r30d.get('wr'))} ({r30d.get('trades', 0)} trades)",
            "",
            f"Profit Factor (7d): {r7d.get('pf', 'N/A')}",
            f"Profit Factor (30d): {r30d.get('pf', 'N/A')}",
            f"EV per trade (30d): {r30d.get('ev_r', 'N/A')}R",
        ]
        return "\n".join(lines)

    def close(self):
        """Clean shutdown."""
        self.bot = None
        self._initialized = False


_telegram_bot: Optional[TelegramBot] = None


def get_telegram_bot(config: dict = None) -> TelegramBot:
    global _telegram_bot
    if _telegram_bot is None:
        _telegram_bot = TelegramBot(config)
    return _telegram_bot
