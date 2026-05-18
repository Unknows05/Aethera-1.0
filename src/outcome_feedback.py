"""
Outcome Feedback Engine — Makes the system truly learn from trade results.

The KEY problem it solves:
- Data was being saved (5728 closed trades) but NOT used to update decision logic
- WR numbers were hardcoded (35.2%, 44.8%, etc) and NEVER updated
- Score thresholds were STATIC regardless of actual performance

This module:
1. Loads REAL win rates from DB (last 7/30 days, not all-time)
2. Updates low/high WR combos dynamically
3. Adjusts score thresholds based on actual performance
4. Tracks regime+direction+session performance over time
5. Computes per-coin WR to detect coin-specific edges
"""
import logging
import sqlite3
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ComboWR:
    win_rate: float
    total: int
    wins: int
    is_stale: bool = False


class OutcomeFeedback:
    def __init__(self, db_path: str = "data/screener.db",
                 cache_path: str = "data/outcome_feedback.json"):
        self.db_path = db_path
        self.cache_path = Path(cache_path)
        self._cache: Dict = {}
        self._cache_time: Optional[datetime] = None
        self._cache_ttl_minutes = 15
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def load_regime_wr(self, days: int = 7) -> Dict[Tuple[str, str], ComboWR]:
        results = {}
        try:
            conn = self._get_conn()
            c = conn.cursor()
            since = (datetime.now() - timedelta(days=days)).isoformat()
            c.execute("""
                SELECT regime, signal, result, COUNT(*) as cnt
                FROM signals
                WHERE timestamp > ? AND result IN ('WIN', 'LOSS')
                GROUP BY regime, signal, result
            """, (since,))
            rows = c.fetchall()

            combo_data = {}
            for r in rows:
                key = (r["regime"], r["signal"])
                if key not in combo_data:
                    combo_data[key] = {"wins": 0, "total": 0}
                combo_data[key]["total"] += r["cnt"]
                if r["result"] == "WIN":
                    combo_data[key]["wins"] += r["cnt"]

            for (regime, signal), data in combo_data.items():
                total = data["total"]
                wins = data["wins"]
                wr = (wins / total * 100) if total > 0 else 50.0
                results[(regime, signal)] = ComboWR(
                    win_rate=wr,
                    total=total,
                    wins=wins,
                    is_stale=total < 10
                )
        except Exception as e:
            logger.error(f"[Feedback] Failed to load regime WR: {e}")
        return results

    def load_session_wr(self, days: int = 14) -> Dict[str, ComboWR]:
        results = {}
        try:
            conn = self._get_conn()
            c = conn.cursor()
            since = (datetime.now() - timedelta(days=days)).isoformat()
            c.execute("""
                SELECT timestamp, result, COUNT(*) as cnt
                FROM signals
                WHERE timestamp > ? AND result IN ('WIN', 'LOSS')
                GROUP BY timestamp, result
            """, (since,))
            rows = c.fetchall()

            from src.session_filter import SessionFilter
            sf = SessionFilter(self.db_path)
            session_data = {}
            for r in rows:
                ts = r["timestamp"]
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    hour = dt.hour
                except Exception:
                    continue
                session_name = sf._hour_to_session(hour)
                if session_name not in session_data:
                    session_data[session_name] = {"wins": 0, "total": 0}
                session_data[session_name]["total"] += r["cnt"]
                if r["result"] == "WIN":
                    session_data[session_name]["wins"] += r["cnt"]

            for session, data in session_data.items():
                total = data["total"]
                wins = data["wins"]
                wr = (wins / total * 100) if total > 0 else 50.0
                results[session] = ComboWR(win_rate=wr, total=total, wins=wins)
        except Exception as e:
            logger.error(f"[Feedback] Failed to load session WR: {e}")
        return results

    def load_coin_wr(self, days: int = 14, min_trades: int = 10
                    ) -> Dict[str, ComboWR]:
        results = {}
        try:
            conn = self._get_conn()
            c = conn.cursor()
            since = (datetime.now() - timedelta(days=days)).isoformat()
            c.execute("""
                SELECT symbol, result, COUNT(*) as cnt
                FROM signals
                WHERE timestamp > ? AND result IN ('WIN', 'LOSS')
                GROUP BY symbol, result
            """, (since,))
            rows = c.fetchall()

            coin_data = {}
            for r in rows:
                sym = r["symbol"]
                if sym not in coin_data:
                    coin_data[sym] = {"wins": 0, "total": 0}
                coin_data[sym]["total"] += r["cnt"]
                if r["result"] == "WIN":
                    coin_data[sym]["wins"] += r["cnt"]

            for sym, data in coin_data.items():
                total = data["total"]
                wins = data["wins"]
                wr = (wins / total * 100) if total > 0 else 50.0
                if total >= min_trades:
                    results[sym] = ComboWR(win_rate=wr, total=total, wins=wins)
        except Exception as e:
            logger.error(f"[Feedback] Failed to load coin WR: {e}")
        return results

    def get_dynamic_regime_signal_wr(self) -> Dict[Tuple[str, str], float]:
        combo_wr = self.load_regime_wr(days=7)
        result = {}
        for (regime, signal), cwr in combo_wr.items():
            result[(regime, signal)] = cwr.win_rate / 100.0
        return result

    def get_adaptive_thresholds(self) -> Dict[str, float]:
        combo_wr = self.load_regime_wr(days=7)
        thresholds = {
            "BULL": 50, "BEAR": 55, "SIDEWAYS": 55, "HIGH_VOL": 55, "DEFAULT": 55
        }
        for (regime, signal), cwr in combo_wr.items():
            if cwr.total < 20:
                continue
            wr = cwr.win_rate
            if signal == "LONG" and wr > 60:
                current = thresholds.get(regime, 55)
                thresholds[regime] = max(45, current - 2)
            elif signal == "LONG" and wr < 45:
                current = thresholds.get(regime, 55)
                thresholds[regime] = min(65, current + 3)
            elif signal == "SHORT" and wr > 60:
                current = thresholds.get(regime, 55)
                thresholds[regime] = max(45, current - 2)
        return thresholds

    def get_position_reduction(self, regime: str, signal: str) -> Tuple[float, str]:
        combo_wr = self.load_regime_wr(days=7)
        key = (regime, signal)
        cwr = combo_wr.get(key)
        if cwr is None or cwr.is_stale:
            fallback_wr = {
                ("SIDEWAYS", "LONG"): 0.352, ("BEAR", "SHORT"): 0.448,
                ("BULL", "SHORT"): 0.663, ("BULL", "LONG"): 0.587,
                ("SIDEWAYS", "SHORT"): 0.626, ("HIGH_VOL", "SHORT"): 0.368,
                ("HIGH_VOL", "LONG"): 0.708, ("BEAR", "LONG"): 0.50,
            }
            wr = fallback_wr.get(key, 0.50)
            position = max(0.25, wr / 0.65)
            return position, f"static:{wr:.1%}"
        wr = cwr.win_rate / 100.0
        if wr < 0.40:
            position = max(0.20, wr / 0.50)
        elif wr < 0.50:
            position = max(0.40, wr / 0.65)
        elif wr < 0.60:
            position = wr / 0.70
        else:
            position = min(1.0, 0.7 + (wr - 0.60) * 3.0)
        source = f"dynamic:{cwr.win_rate:.1f}%({cwr.total})"
        return round(position, 2), source

    def get_confidence_penalty(self, regime: str, signal: str) -> Tuple[int, str]:
        combo_wr = self.load_regime_wr(days=7)
        key = (regime, signal)
        cwr = combo_wr.get(key)
        if cwr is None or cwr.is_stale:
            return 0, "insufficient_data"
        wr = cwr.win_rate
        if wr >= 60:
            bonus = min(5, int((wr - 55) * 0.5))
            return -bonus, f"high_wr:{wr:.1f}%"
        elif wr < 38:
            penalty = min(12, int((50 - wr) * 0.5))
            return penalty, f"low_wr:{wr:.1f}%"
        elif wr < 45:
            penalty = min(7, int((50 - wr) * 0.3))
            return penalty, f"below_avg:{wr:.1f}%"
        return 0, f"avg:{wr:.1f}%"

    def save_feedback_report(self):
        combo_wr = self.load_regime_wr(days=7)
        session_wr = self.load_session_wr(days=14)
        coin_wr = self.load_coin_wr(days=14, min_trades=10)
        report = {
            "timestamp": datetime.now().isoformat(),
            "regime_signal_wr": {
                f"{k[0]}_{k[1]}": {
                    "wr": round(v.win_rate, 1),
                    "total": v.total,
                    "wins": v.wins,
                    "stale": v.is_stale
                }
                for k, v in combo_wr.items()
            },
            "session_wr": {
                k: {"wr": round(v.win_rate, 1), "total": v.total, "wins": v.wins}
                for k, v in session_wr.items()
            },
            "coin_wr": {
                k: {"wr": round(v.win_rate, 1), "total": v.total, "wins": v.wins}
                for k, v in coin_wr.items()
            },
            "adaptive_thresholds": self.get_adaptive_thresholds(),
        }
        try:
            with open(self.cache_path, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"[Feedback] Saved report to {self.cache_path}")
        except Exception as e:
            logger.error(f"[Feedback] Save failed: {e}")

    def get_report(self) -> dict:
        combo_wr = self.load_regime_wr(days=7)
        session_wr = self.load_session_wr(days=14)
        return {
            "regime_signal": {
                f"{k[0]}+{k[1]}": {
                    "wr": round(v.win_rate, 1),
                    "trades": v.total
                }
                for k, v in combo_wr.items()
            },
            "session": {
                k: {"wr": round(v.win_rate, 1), "trades": v.total}
                for k, v in session_wr.items()
            },
        }


_feedback_instance: Optional[OutcomeFeedback] = None


def get_feedback(db_path: str = "data/screener.db") -> OutcomeFeedback:
    global _feedback_instance
    if _feedback_instance is None:
        _feedback_instance = OutcomeFeedback(db_path)
    return _feedback_instance


# ═══ PERFORMANCE GUARDS (Meridian-inspired) ════════════════════════

def validate_performance_record(perf_data: dict) -> Tuple[bool, str]:
    """
    Validate performance record before saving to prevent data corruption.
    
    Guards:
    1. Unit mix detection (SOL vs USD confusion)
    2. Absurd PnL detection (>100% loss without stop loss)
    3. Timestamp sanity check
    4. Invalid duration detection
    
    Returns: (is_valid, reason)
    """
    # Guard 1: Unit mix detection
    initial_usd = perf_data.get('initial_value_usd', 0)
    final_usd = perf_data.get('final_value_usd', 0)
    amount_sol = perf_data.get('amount_sol', 0)
    
    if (initial_usd >= 20 and 
        amount_sol >= 0.25 and 
        0 < final_usd <= amount_sol * 2):
        # Suspicious: final_value_usd looks like SOL amount
        return False, f"Unit mix detected: final_value_usd={final_usd} looks like SOL amount"
    
    # Guard 2: Absurd PnL detection
    pnl_pct = perf_data.get('pnl_pct', 0)
    close_reason = str(perf_data.get('close_reason', '')).lower()
    
    if initial_usd >= 20 and pnl_pct <= -90:
        if 'stop loss' not in close_reason and 'sl' not in close_reason:
            return False, f"Absurd PnL: {pnl_pct}% without stop loss trigger"
    
    if pnl_pct > 1000:
        return False, f"Suspicious profit: {pnl_pct}% > 1000%"
    
    # Guard 3: Timestamp sanity
    held_hours = perf_data.get('held_hours', 0)
    if held_hours < 0:
        return False, f"Negative held_hours: {held_hours}"
    if held_hours > 720:  # Max 30 days
        return False, f"Excessive held_hours: {held_hours} (> 30 days)"
    
    # Guard 4: Price sanity
    entry_price = perf_data.get('entry_price', 0)
    exit_price = perf_data.get('exit_price', 0)
    
    if entry_price <= 0:
        return False, f"Invalid entry_price: {entry_price}"
    if exit_price < 0:
        return False, f"Invalid exit_price: {exit_price}"
    if exit_price > entry_price * 10:
        return False, f"Suspicious exit_price: {exit_price} > 10x entry"
    
    return True, "Valid"


def record_performance_with_guards(
    symbol: str,
    regime: str,
    signal: str,
    result: str,
    pnl_pct: float,
    confidence: float,
    composite_score: float,
    entry_price: float,
    exit_price: float,
    held_hours: float,
    close_reason: str,
    db_path: str = "data/screener.db"
) -> bool:
    """
    Record performance with comprehensive validation guards.
    Returns True if recorded, False if rejected.
    """
    perf_data = {
        'symbol': symbol,
        'regime': regime,
        'signal': signal,
        'pnl_pct': pnl_pct,
        'initial_value_usd': entry_price * 100,  # Approximate
        'final_value_usd': exit_price * 100,
        'held_hours': held_hours,
        'close_reason': close_reason,
        'entry_price': entry_price,
        'exit_price': exit_price,
    }
    
    is_valid, reason = validate_performance_record(perf_data)
    
    if not is_valid:
        logger.warning(f"[PerformanceGuard] Rejected record for {symbol}: {reason}")
        return False
    
    # Record is valid, proceed with normal recording
    lesson_text = None
    try:
        fb = get_feedback(db_path)
        # Add to lessons if notable outcome
        if abs(pnl_pct) > 10 or result == 'LOSS':
            from src.lessons import add_lesson
            lesson_text = generate_lesson_from_trade(
                symbol, regime, signal, result, pnl_pct, 
                confidence, held_hours, close_reason
            )
            if lesson_text:
                add_lesson(lesson_text, symbol=symbol, regime=regime, signal=signal, result=result)

        # ── Audit Chain: log trade outcome ──
        try:
            from src.audit_chain import get_audit_chain
            chain = get_audit_chain()
            chain.append({
                "type": "trade_outcome",
                "symbol": symbol,
                "regime": regime,
                "signal": signal,
                "result": result,
                "pnl_pct": round(pnl_pct, 2),
                "confidence": confidence,
                "held_hours": held_hours,
                "close_reason": close_reason,
            })
        except Exception:
            pass

        # ── HiveMind: push lesson to swarm ──
        try:
            swarm_url = os.getenv("HIVEMIND_URL", "")
            if swarm_url and lesson_text:
                from src.hivemind_client import get_hivemind
                hive = get_hivemind()
                hive.push_lesson(
                    rule=lesson_text,
                    tags=[symbol, regime, signal],
                    regime=regime,
                    signal=signal,
                    result=result,
                    pnl_pct=pnl_pct,
                    confidence=confidence,
                )
                from src.audit_chain import get_audit_chain
                chain = get_audit_chain()
                chain.append({
                    "type": "lesson_pushed",
                    "symbol": symbol,
                    "regime": regime,
                    "signal": signal,
                    "rule": lesson_text[:200],
                })
        except Exception:
            pass

        return True
        
    except Exception as e:
        logger.error(f"[PerformanceGuard] Error recording: {e}")
        return False


def generate_lesson_from_trade(
    symbol: str, regime: str, signal: str, result: str,
    pnl_pct: float, confidence: float, held_hours: float,
    close_reason: str
) -> Optional[str]:
    """Generate structured lesson from trade outcome (Meridian pattern)."""
    
    win = result == 'WIN'
    
    # Only generate lessons for notable outcomes
    if not win and pnl_pct > -3:
        return None  # Small loss, not educational
    if win and pnl_pct < 5:
        return None  # Small win, not notable
    
    if win:
        if pnl_pct > 15:
            return (f"Strong win: {signal} on {symbol} in {regime} regime "
                   f"yielded +{pnl_pct:.1f}%. High confidence ({confidence:.0f}%) "
                   f"signals in {regime} are reliable. Held {held_hours:.1f}h.")
        else:
            return (f"Win: {signal} on {symbol} in {regime} regime "
                   f"(+{pnl_pct:.1f}%, held {held_hours:.1f}h). "
                   f"Reasonable outcome for this combo.")
    else:
        if pnl_pct < -10:
            return (f"Large loss: {signal} on {symbol} in {regime} regime "
                   f"({pnl_pct:.1f}%). {regime}+{signal} is a dangerous combo. "
                   f"Close reason: {close_reason}. Reduce size or avoid this combo.")
        else:
            return (f"Loss: {signal} on {symbol} in {regime} regime "
                   f"({pnl_pct:.1f}%). Consider tighter SL or higher confidence threshold.")


# Export untuk backward compatibility
__all__ = [
    'OutcomeFeedback', 'get_feedback', 'ComboWR',
    'validate_performance_record', 'record_performance_with_guards'
]