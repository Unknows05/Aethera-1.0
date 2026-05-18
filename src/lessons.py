"""
Lessons Engine — generates structured lessons from closed trades.
Each closed trade produces a 3-5 sentence insight injected into future decisions.
"""
import json
import os
from datetime import datetime
from typing import Dict, List

LESSONS_PATH = "data/lessons.json"


def generate_lesson(symbol: str, regime: str, signal: str, result: str,
                     pnl_pct: float, confidence: float, held_hours: float = 0) -> str:
    """Generate a lesson from a closed trade outcome."""
    win = result == "WIN"
    pnl_r = pnl_pct

    if win:
        if pnl_r > 3:
            return (f"Strong win: {signal} on {symbol} in {regime} regime "
                    f"yielded +{pnl_r:.1f}%. High confidence ({confidence:.0f}%) "
                    f"signals in {regime} are reliable. Consider increasing size "
                    f"on similar setups.")
        elif pnl_r > 0:
            return (f"Win: {signal} on {symbol} in {regime} regime "
                    f"(+{pnl_r:.1f}%, held {held_hours:.1f}h). "
                    f"{regime}+{signal} is a valid combo but returns are modest. "
                    f"Target entries with better R:R.")
        else:
            return (f"Break-even: {signal} on {symbol} in {regime} regime. "
                    f"Check if SL was too tight or entry timing was off.")
    else:
        if pnl_r < -5:
            return (f"Large loss: {signal} on {symbol} in {regime} regime "
                    f"({pnl_r:.1f}%). {regime}+{signal} is a dangerous combo. "
                    f"Never trade {signal} in {regime} when confidence < 55%. "
                    f"Reduce position size on this regime+signal combo.")
        else:
            return (f"Loss: {signal} on {symbol} in {regime} regime "
                    f"({pnl_r:.1f}%). Consider tighter SL or higher "
                    f"confidence threshold for this combo.")


def add_lesson(lesson: str, symbol: str = "", regime: str = "",
               signal: str = "", result: str = ""):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "regime": regime,
        "signal": signal,
        "result": result,
        "lesson": lesson,
    }
    lessons = _load()
    lessons.append(entry)
    if len(lessons) > 500:
        lessons = lessons[-500:]
    os.makedirs("data", exist_ok=True)
    with open(LESSONS_PATH, "w") as f:
        json.dump(lessons, f, indent=2)


def get_recent_lessons(limit: int = 20) -> List[Dict]:
    return _load()[-limit:]


def get_lessons_summary() -> str:
    """Generate a summary of recent lessons for injection into system context."""
    lessons = _load()[-20:]
    if not lessons:
        return ""
    wins = sum(1 for l in lessons if l.get("result") == "WIN")
    losses = sum(1 for l in lessons if l.get("result") == "LOSS")
    wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    summary = f"Recent 20 trades: {wins}W/{losses}L (WR {wr:.0f}%). Key lessons:\n"
    for l in lessons[-5:]:
        summary += f"  - {l['lesson'][:200]}\n"
    return summary


def _load() -> list:
    if os.path.exists(LESSONS_PATH):
        try:
            with open(LESSONS_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return []
