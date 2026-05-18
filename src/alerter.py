"""
Alerter — Detect new/lost signals and generate notifications.
Compares current scan with previous scan to find changes.
"""
import logging
import copy

logger = logging.getLogger(__name__)


class SignalAlerter:
    """Detect signal changes between scans."""

    def __init__(self):
        self._previous_signals: dict[str, dict] = {}
        self._alerts: list[dict] = []

    def check(self, current_results: list[dict], timestamp: str) -> list[dict]:
        """
        Compare current scan with previous scan.
        Returns list of alert dicts.
        """
        alerts = []
        current_map = {r["symbol"]: r for r in current_results}

        # Detect NEW signals (was WAIT → now LONG/SHORT)
        for sym, data in current_map.items():
            sig = data.get("signal", "WAIT")
            if sig in ("LONG", "SHORT"):
                prev = self._previous_signals.get(sym)
                if prev is None or prev.get("signal") == "WAIT":
                    alert = {
                        "type": "NEW",
                        "symbol": sym,
                        "signal": sig,
                        "confidence": data.get("confidence", 0),
                        "price": data.get("price", 0),
                        "regime": data.get("regime", ""),
                        "timestamp": timestamp,
                        "details": self._build_details(data),
                    }
                    alerts.append(alert)
                    logger.info(f"[Alert] 🆕 NEW {sig}: {sym} ({alert['confidence']}%)")

        # Detect LOST signals (was LONG/SHORT → now WAIT or gone)
        for sym, prev in self._previous_signals.items():
            prev_sig = prev.get("signal", "WAIT")
            if prev_sig in ("LONG", "SHORT"):
                curr = current_map.get(sym)
                curr_sig = curr.get("signal", "WAIT") if curr else "WAIT"
                if curr_sig != prev_sig:
                    alert = {
                        "type": "LOST",
                        "symbol": sym,
                        "signal": prev_sig,
                        "confidence": prev.get("confidence", 0),
                        "price": prev.get("price", 0),
                        "regime": prev.get("regime", ""),
                        "timestamp": timestamp,
                        "details": f"Changed from {prev_sig} to {curr_sig}",
                    }
                    alerts.append(alert)
                    logger.info(f"[Alert] ❌ LOST {prev_sig}: {sym} → {curr_sig}")

        self._previous_signals = copy.deepcopy(current_map)
        self._alerts = alerts
        return alerts

    def _build_details(self, data: dict) -> str:
        """Build human-readable alert details."""
        parts = []
        if data.get("regime"):
            parts.append(f"Regime: {data['regime']}")
        if data.get("patterns_detected"):
            parts.append("Patterns: " + ", ".join(data["patterns_detected"]))
        if data.get("reasons"):
            parts.append("Reasons: " + ", ".join(data["reasons"][:2]))
        if data.get("sl") and data.get("tp"):
            parts.append(f"SL: {data['sl']:.4f} | TP: {data['tp']:.4f}")
        return " | ".join(parts)

    def get_latest_alerts(self, limit: int = 20) -> list[dict]:
        """Get recent alerts from current session."""
        return self._alerts[:limit]

    def format_alerts(self, alerts: list[dict]) -> str:
        """Format alerts for display/notification."""
        if not alerts:
            return "No new signal changes."

        lines = [f"📊 Signal Update ({len(alerts)} changes)", ""]
        for a in alerts:
            if a["type"] == "NEW":
                emoji = "🟢" if a["signal"] == "LONG" else "🔴"
                lines.append(f"{emoji} NEW {a['signal']}: {a['symbol']}")
                lines.append(f"   Confidence: {a['confidence']}% | Price: ${a['price']}")
                lines.append(f"   {a['details']}")
            else:
                lines.append(f"❌ LOST {a['signal']}: {a['symbol']} → changed")
                lines.append(f"   {a['details']}")
            lines.append("")

        return "\n".join(lines)
