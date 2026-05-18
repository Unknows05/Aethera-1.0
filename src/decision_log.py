"""
Decision Log — records WHY every signal was generated or blocked.
Structured rationale for audit and learning.
"""
import json
import os
from datetime import datetime
from typing import Dict, Optional

DECISION_LOG_PATH = "data/decision_log.json"


def log_decision(symbol: str, signal: str, confidence: float, regime: str,
                 composite_score: float, reasons: list, ml_confidence: float = 0,
                 blocked: bool = False, block_reason: str = "") -> Dict:
    entry = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "decision": signal,
        "confidence": confidence,
        "regime": regime,
        "composite_score": composite_score,
        "ml_confidence": ml_confidence,
        "blocked": blocked,
        "block_reason": block_reason,
        "reasons": reasons[:4],
    }
    _append(entry)
    return entry


def get_recent_decisions(limit: int = 50) -> list:
    entries = _load()
    return entries[-limit:]


def get_decisions_by_symbol(symbol: str, limit: int = 20) -> list:
    entries = _load()
    return [e for e in entries if e["symbol"] == symbol][-limit:]


def _load() -> list:
    if os.path.exists(DECISION_LOG_PATH):
        try:
            with open(DECISION_LOG_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _append(entry: dict):
    entries = _load()
    entries.append(entry)
    if len(entries) > 5000:
        entries = entries[-5000:]
    os.makedirs("data", exist_ok=True)
    with open(DECISION_LOG_PATH, "w") as f:
        json.dump(entries, f, indent=2)
