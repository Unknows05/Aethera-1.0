"""
Lesson Manager — manages lessons as Markdown files in vault/lessons/.
Each closed trade produces a lesson file with structured frontmatter.
"""
import os
import re
import yaml
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LessonManager:
    """Manages lessons in vault/lessons/ as Markdown files."""

    def __init__(self, vault_dir: str = "vault", indexer=None):
        self.lessons_dir = os.path.join(vault_dir, "lessons")
        os.makedirs(self.lessons_dir, exist_ok=True)
        self.indexer = indexer

    def create_lesson(self, symbol: str, regime: str, signal: str,
                      result: str, pnl_pct: float, confidence: float,
                      held_hours: float = 0, exit_reason: str = "",
                      lesson_text: str = "") -> str:
        """Create a lesson file for a closed trade. Returns filename."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        safe_symbol = re.sub(r'[^a-zA-Z0-9]', '', symbol)
        filename = f"{date_str}-{safe_symbol}-{signal}-{result}.md"
        filepath = os.path.join(self.lessons_dir, filename)

        # Generate lesson text if not provided
        if not lesson_text:
            lesson_text = self._generate_lesson(symbol, regime, signal, result,
                                                pnl_pct, confidence, held_hours)

        frontmatter = {
            "title": f"{symbol} {signal} — {result}",
            "symbol": symbol,
            "regime": regime,
            "signal": signal,
            "result": result,
            "confidence": confidence,
            "held_hours": round(held_hours, 2),
            "exit_reason": exit_reason,
            "created_at": datetime.now().isoformat(),
        }

        body = f"""## Outcome
{result} | PnL: {pnl_pct:+.2f}% | Confidence: {confidence:.0f}% | Held: {held_hours:.1f}h

## Lesson
{lesson_text}

## Context
Regime: {regime}
Signal Type: {signal}
Exit Reason: {exit_reason or 'N/A'}
"""

        content = "---\n" + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).rstrip() + "\n---\n\n" + body

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # Index if indexer available
        if self.indexer:
            rel_path = os.path.relpath(filepath, "vault")
            self.indexer.index_single(rel_path, filepath)

        logger.info(f"[LessonManager] Created lesson: {filename}")
        return filename

    def _generate_lesson(self, symbol: str, regime: str, signal: str,
                         result: str, pnl_pct: float, confidence: float,
                         held_hours: float = 0) -> str:
        """Generate lesson text from trade outcome."""
        win = result == "WIN"

        if win:
            if pnl_pct > 3:
                return (f"Strong win: {signal} on {symbol} in {regime} regime "
                        f"yielded +{pnl_pct:.1f}%. High confidence ({confidence:.0f}%) "
                        f"signals in {regime} are reliable. Consider increasing size "
                        f"on similar setups.")
            elif pnl_pct > 0:
                return (f"Win: {signal} on {symbol} in {regime} regime "
                        f"(+{pnl_pct:.1f}%, held {held_hours:.1f}h). "
                        f"{regime}+{signal} is a valid combo but returns are modest. "
                        f"Target entries with better R:R.")
            else:
                return (f"Break-even: {signal} on {symbol} in {regime} regime. "
                        f"Check if SL was too tight or entry timing was off.")
        else:
            if pnl_pct < -5:
                return (f"Large loss: {signal} on {symbol} in {regime} regime "
                        f"({pnl_pct:.1f}%). {regime}+{signal} is a dangerous combo. "
                        f"Never trade {signal} in {regime} when confidence < 55%. "
                        f"Reduce position size on this regime+signal combo.")
            else:
                return (f"Loss: {signal} on {symbol} in {regime} regime "
                        f"({pnl_pct:.1f}%). Consider tighter SL or higher "
                        f"confidence threshold for this combo.")

    def get_lesson(self, filename: str) -> Optional[Dict]:
        """Get a lesson by filename."""
        filepath = os.path.join(self.lessons_dir, filename)
        if not os.path.exists(filepath):
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
        if not match:
            return None

        frontmatter = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
        return {**frontmatter, "body": body, "filename": filename}

    def get_recent_lessons(self, limit: int = 20,
                           regime: Optional[str] = None,
                           signal: Optional[str] = None,
                           result: Optional[str] = None) -> List[Dict]:
        """Get recent lessons, optionally filtered."""
        lessons = []
        if not os.path.isdir(self.lessons_dir):
            return lessons

        files = sorted(os.listdir(self.lessons_dir), reverse=True)
        for filename in files:
            if not filename.endswith(".md"):
                continue
            try:
                lesson = self.get_lesson(filename)
                if lesson is None:
                    continue
                if regime and lesson.get("regime", "").upper() != regime.upper():
                    continue
                if signal and lesson.get("signal", "").upper() != signal.upper():
                    continue
                if result and lesson.get("result", "").upper() != result.upper():
                    continue
                lessons.append(lesson)
                if len(lessons) >= limit:
                    break
            except Exception:
                continue
        return lessons

    def get_lessons_summary(self, limit: int = 20) -> str:
        """Generate a summary of recent lessons for LLM prompt injection."""
        lessons = self.get_recent_lessons(limit=limit)
        if not lessons:
            return ""

        wins = sum(1 for l in lessons if l.get("result") == "WIN")
        losses = sum(1 for l in lessons if l.get("result") == "LOSS")
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

        summary = f"Recent {limit} trades: {wins}W/{losses}L (WR {wr:.0f}%). Key lessons:\n"
        for l in lessons[-5:]:
            summary += f"  - {l.get('body', '')[:200]}\n"
        return summary

    def get_stats(self) -> Dict:
        """Return lesson statistics."""
        if not os.path.isdir(self.lessons_dir):
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}

        wins = 0
        losses = 0
        for filename in os.listdir(self.lessons_dir):
            if not filename.endswith(".md"):
                continue
            if "-WIN.md" in filename:
                wins += 1
            elif "-LOSS.md" in filename:
                losses += 1

        total = wins + losses
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        }
