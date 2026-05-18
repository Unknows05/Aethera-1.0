"""
Curator — periodic knowledge nudge system (Hermes-inspired).
Checks if the agent should persist new knowledge after every N closed trades.
"""
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.database import get_db

logger = logging.getLogger(__name__)

CURATOR_STATE_PATH = "data/curator_state.json"
NUDGE_INTERVAL_TRADES = 10


class Curator:
    """Periodic knowledge nudge — checks if agent should persist new learning."""

    def __init__(self, state_path: str = CURATOR_STATE_PATH,
                 nudge_interval: int = NUDGE_INTERVAL_TRADES):
        self.state_path = state_path
        self.nudge_interval = nudge_interval
        os.makedirs("data", exist_ok=True)
        self._last_nudge_ts = None

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_nudge_ts": None, "trade_count_at_nudge": 0}

    def _save_state(self, state: dict):
        with open(self.state_path, "w") as f:
            json.dump(state, f, indent=2)

    def _get_closed_trade_count(self) -> int:
        """Get count of closed trades (WIN/LOSS) from the database."""
        db = get_db()
        if db is None:
            return 0
        try:
            c = db.conn.cursor()
            c.execute("SELECT COUNT(*) FROM signals WHERE result IN ('WIN','LOSS')")
            row = c.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"[Curator] DB read error: {e}")
            return 0

    def _get_closed_trades_since(self, since_ts: Optional[str]) -> int:
        """Count closed trades since a given timestamp."""
        db = get_db()
        if db is None or since_ts is None:
            return 0
        try:
            c = db.conn.cursor()
            c.execute(
                """SELECT COUNT(*) FROM signals
                   WHERE result IN ('WIN','LOSS') AND exit_timestamp > ?""",
                (since_ts,),
            )
            row = c.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"[Curator] DB read error: {e}")
            return 0

    def _get_recent_trades_summary(self, limit: int = 10) -> str:
        """Get a summary of recent closed trades for the nudge prompt."""
        db = get_db()
        if db is None:
            return ""
        try:
            c = db.conn.cursor()
            c.execute(
                """SELECT symbol, signal, regime, result, pnl_pct, exit_reason, exit_timestamp
                   FROM signals WHERE result IN ('WIN','LOSS')
                   ORDER BY exit_timestamp DESC LIMIT ?""",
                (limit,),
            )
            rows = c.fetchall()
            if not rows:
                return ""
            lines = []
            for r in rows:
                lines.append(
                    f"{r['symbol']} {r['signal']} in {r['regime']}: {r['result']} "
                    f"({r['pnl_pct']:+.2f}%, {r['exit_reason'] or 'unknown'})"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"[Curator] DB read error: {e}")
            return ""

    def should_nudge(self) -> bool:
        """Returns True every N closed trades since the last nudge."""
        state = self._load_state()
        total_closed = self._get_closed_trade_count()
        if total_closed == 0:
            return False

        trade_count_at_nudge = state.get("trade_count_at_nudge", 0)
        trades_since_nudge = total_closed - trade_count_at_nudge

        if trades_since_nudge >= self.nudge_interval:
            return True
        return False

    def generate_nudge_prompt(self) -> str:
        """Build a prompt for the LLM to decide if new knowledge should be persisted."""
        total_closed = self._get_closed_trade_count()
        state = self._load_state()
        trade_count_at_nudge = state.get("trade_count_at_nudge", 0)
        trades_since = total_closed - trade_count_at_nudge

        # Count days spanned
        days_span = "unknown"
        last_nudge = state.get("last_nudge_ts")
        if last_nudge:
            try:
                since_dt = datetime.fromisoformat(last_nudge)
                delta = datetime.now() - since_dt
                days_span = f"{delta.days} days"
            except Exception:
                days_span = "unknown"

        recent_trades = self._get_recent_trades_summary(limit=10)

        prompt = (
            f"You have completed {trades_since} trades over the last {days_span}. "
            f"Total closed trades: {total_closed}.\n\n"
            f"Recent trades:\n{recent_trades}\n\n"
            f"Analyze the recent performance. Respond with ONE of the following JSON actions:\n"
            f'1. {{"action": "create_skill", "name": "...", "description": "...", '
            f'"tags": [...], "regime": "...", "signal": "...", '
            f'"procedure": "...", "pitfalls": "...", "evidence": "..."}}\n'
            f'2. {{"action": "update_memory", "entry": "..."}}\n'
            f'3. {{"action": "none"}}\n\n'
            f"Only respond with valid JSON. No explanation."
        )
        return prompt

    def apply_nudge_decision(self, decision: dict):
        """Apply the LLM's nudge decision and update curator state."""
        action = decision.get("action", "none")

        if action == "create_skill":
            from src.skill_creator import SkillCreator
            sc = SkillCreator()
            name = decision.get("name", "untitled")
            filename = None
            try:
                filename = sc.create_skill(
                    name=name,
                    description=decision.get("description", ""),
                    tags=decision.get("tags", []),
                    regime=decision.get("regime"),
                    signal=decision.get("signal"),
                    procedure=decision.get("procedure", ""),
                    pitfalls=decision.get("pitfalls", ""),
                    evidence=decision.get("evidence", ""),
                )
                logger.info(f"[Curator] Created skill: {filename}")

                # ── Audit Chain: log skill created ──
                try:
                    from src.audit_chain import get_audit_chain
                    chain = get_audit_chain()
                    chain.append({
                        "type": "skill_created",
                        "name": name,
                        "regime": decision.get("regime", ""),
                        "signal": decision.get("signal", ""),
                        "description": decision.get("description", "")[:200],
                    })
                except Exception:
                    pass

                # ── HiveMind: push skill as lesson ──
                try:
                    import os
                    if os.getenv("HIVEMIND_URL", ""):
                        from src.hivemind_client import get_hivemind
                        hive = get_hivemind()
                        hive.push_lesson(
                            rule=f"[Skill] {name}: {decision.get('description', '')[:300]}",
                            tags=decision.get("tags", []),
                            regime=decision.get("regime", ""),
                            signal=decision.get("signal", ""),
                            result="SKILL",
                            pnl_pct=0,
                            confidence=0,
                        )
                        chain = get_audit_chain()
                        chain.append({
                            "type": "lesson_pushed",
                            "skill": name,
                            "regime": decision.get("regime", ""),
                            "signal": decision.get("signal", ""),
                            "rule": f"[Skill] {name}"[:200],
                        })
                except Exception:
                    pass

            except ValueError as e:
                logger.warning(f"[Curator] Skill creation skipped: {e}")

        elif action == "update_memory":
            from src.agent_memory import AgentMemory
            mem = AgentMemory()
            entry = decision.get("entry", "")
            if entry:
                ok = mem.add_entry(entry)
                if not ok:
                    mem.consolidate()
                    mem.add_entry(entry)
                logger.info(f"[Curator] Updated memory with entry: {entry[:80]}...")

        # Update state
        state = self._load_state()
        state["last_nudge_ts"] = datetime.now().isoformat()
        state["trade_count_at_nudge"] = self._get_closed_trade_count()
        self._save_state(state)
