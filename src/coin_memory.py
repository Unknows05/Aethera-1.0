"""
Coin Memory — per-symbol trading history and performance tracking.
Pattern: recall_coin(symbol) → {trades, WR, best_regime, worst_regime, notes}
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

COIN_MEMORY_PATH = "data/coin_memory.json"


def record_trade(symbol: str, regime: str, signal: str, result: str,
                 pnl_pct: float, confidence: float, held_hours: float = 0):
    """Record a completed trade in coin memory."""
    memory = _load()
    if symbol not in memory:
        memory[symbol] = {"trades": [], "notes": []}

    entry = {
        "timestamp": datetime.now().isoformat(),
        "regime": regime,
        "signal": signal,
        "result": result,
        "pnl_pct": round(pnl_pct, 2),
        "confidence": confidence,
        "held_hours": round(held_hours, 2),
    }
    memory[symbol]["trades"].append(entry)
    if len(memory[symbol]["trades"]) > 100:
        memory[symbol]["trades"] = memory[symbol]["trades"][-100:]

    # Auto-generate note on significant events
    combo = f"{regime}+{signal}"
    combo_trades = [t for t in memory[symbol]["trades"] if t["regime"] == regime and t["signal"] == signal]
    combo_wins = sum(1 for t in combo_trades if t["result"] == "WIN")
    combo_total = len(combo_trades)
    if combo_total >= 10:
        wr = combo_wins / combo_total * 100
        if wr < 35:
            note = f"[AUTO] {combo}: {combo_wins}/{combo_total} ({wr:.0f}% WR). HIGH RISK. Reduce size on this combo."
            if note not in memory[symbol]["notes"]:
                memory[symbol]["notes"].append(note)
        elif wr > 65:
            note = f"[AUTO] {combo}: {combo_wins}/{combo_total} ({wr:.0f}% WR). Reliable. Consider higher size."
            if note not in memory[symbol]["notes"]:
                memory[symbol]["notes"].append(note)

    _save(memory)


def recall_coin(symbol: str) -> dict:
    """Get per-symbol trading history and stats."""
    memory = _load()
    entry = memory.get(symbol, {"trades": [], "notes": []})
    trades = entry.get("trades", [])

    if not trades:
        return {"symbol": symbol, "total_trades": 0, "message": "No history for this coin"}

    wins = sum(1 for t in trades if t["result"] == "WIN")
    losses = sum(1 for t in trades if t["result"] == "LOSS")
    wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
    avg_pnl = sum(t["pnl_pct"] for t in trades) / len(trades) if trades else 0

    # Best/worst regime
    by_regime = {}
    for t in trades:
        key = f"{t['regime']}+{t['signal']}"
        if key not in by_regime:
            by_regime[key] = {"trades": 0, "wins": 0, "pnls": []}
        by_regime[key]["trades"] += 1
        if t["result"] == "WIN":
            by_regime[key]["wins"] += 1
        by_regime[key]["pnls"].append(t["pnl_pct"])

    regime_stats = {}
    for k, v in by_regime.items():
        regime_stats[k] = {
            "trades": v["trades"],
            "wr": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0,
            "avg_pnl": round(sum(v["pnls"]) / len(v["pnls"]), 2),
        }

    sorted_by_wr = sorted(regime_stats.items(), key=lambda x: x[1]["wr"], reverse=True)

    recent_10 = trades[-10:]
    recent_wr = sum(1 for t in recent_10 if t["result"] == "WIN") / len(recent_10) * 100 if recent_10 else 0

    return {
        "symbol": symbol,
        "total_trades": len(trades),
        "wins": wins, "losses": losses,
        "win_rate": round(wr, 1),
        "avg_pnl": round(avg_pnl, 2),
        "recent_10_wr": round(recent_wr, 1),
        "best_combo": f"{sorted_by_wr[0][0]} ({sorted_by_wr[0][1]['wr']}%)" if sorted_by_wr else "N/A",
        "worst_combo": f"{sorted_by_wr[-1][0]} ({sorted_by_wr[-1][1]['wr']}%)" if sorted_by_wr else "N/A",
        "notes": entry.get("notes", [])[-5:],
    }


def get_coin_note_for_prompt(symbol: str) -> str:
    """Format coin memory as prompt context string."""
    info = recall_coin(symbol)
    if info.get("total_trades", 0) == 0:
        return ""
    lines = [
        f"Coin: {symbol} | {info['total_trades']} trades | WR: {info['win_rate']}% | Avg PnL: {info['avg_pnl']}%",
        f"Recent 10 WR: {info['recent_10_wr']}% | Best: {info['best_combo']} | Worst: {info['worst_combo']}",
    ]
    for note in info.get("notes", []):
        lines.append(f"  Note: {note}")
    return "\n".join(lines)


def _load() -> dict:
    if os.path.exists(COIN_MEMORY_PATH):
        try:
            with open(COIN_MEMORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(COIN_MEMORY_PATH, "w") as f:
        json.dump(data, f, indent=2)
