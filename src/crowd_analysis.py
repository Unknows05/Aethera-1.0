"""
Crowd Analysis Module — Analyzes HiveMind swarm lessons to auto-create skills.

Pulls lessons from the swarm, identifies consensus patterns, and generates
proven skills when crowd agreement is strong enough.
"""
import logging
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional

from src.hivemind_client import get_hivemind

logger = logging.getLogger(__name__)

# Minimum consensus threshold to auto-create a skill
MIN_CONSENSUS = 0.70  # 70% of crowd agrees
MIN_LESSON_COUNT = 5  # Minimum lessons to analyze


def analyze_crowd(lessons: List[Dict]) -> Dict[str, Dict]:
    """Analyze swarm lessons to find consensus patterns.

    Returns dict keyed by "{regime}_{signal}" with pattern stats.
    """
    patterns = defaultdict(lambda: {
        "regime": "",
        "signal": "",
        "total": 0,
        "wins": 0,
        "losses": 0,
        "avg_confidence": 0.0,
        "avg_held_hours": 0.0,
        "exit_reasons": defaultdict(int),
        "symbols": set(),
        "lessons": [],
    })

    for lesson in lessons:
        regime = lesson.get("regime", "UNKNOWN")
        signal = lesson.get("signal", "UNKNOWN")
        result = lesson.get("result", "")
        key = f"{regime}_{signal}"

        p = patterns[key]
        p["regime"] = regime
        p["signal"] = signal
        p["total"] += 1

        if result.upper() == "WIN":
            p["wins"] += 1
        elif result.upper() in ("LOSS", "LOSE"):
            p["losses"] += 1

        conf = lesson.get("confidence", 50)
        p["avg_confidence"] = (p["avg_confidence"] * (p["total"] - 1) + conf) / p["total"]

        held = lesson.get("held_hours", 0)
        p["avg_held_hours"] = (p["avg_held_hours"] * (p["total"] - 1) + held) / p["total"]

        exit_reason = lesson.get("exit_reason", "")
        if exit_reason:
            p["exit_reasons"][exit_reason] += 1

        symbol = lesson.get("symbol", "")
        if symbol:
            p["symbols"].add(symbol)

        p["lessons"].append(lesson.get("rule", ""))

    # Compute derived stats
    for key, p in patterns.items():
        total = p["wins"] + p["losses"]
        p["win_rate"] = (p["wins"] / total * 100) if total > 0 else 0
        p["consensus"] = max(p["wins"], p["losses"]) / total if total > 0 else 0
        p["symbols"] = list(p["symbols"])[:10]  # Limit to 10
        p["top_exit_reason"] = max(p["exit_reasons"].items(), key=lambda x: x[1])[0] if p["exit_reasons"] else "unknown"
        p["exit_reasons"] = dict(p["exit_reasons"])

    return dict(patterns)


def extract_skills(patterns: Dict[str, Dict], min_consensus: float = MIN_CONSENSUS,
                   min_count: int = MIN_LESSON_COUNT) -> List[Dict]:
    """Extract auto-skills from crowd patterns that meet consensus threshold."""
    skills = []

    for key, p in patterns.items():
        if p["total"] < min_count:
            continue
        if p["consensus"] < min_consensus:
            continue

        outcome = "WIN" if p["wins"] >= p["losses"] else "LOSS"
        win_rate = p["win_rate"]

        # Build skill from crowd consensus
        skill = {
            "name": f"Crowd {p['regime']} {p['signal']} ({outcome})",
            "description": f"Swarm consensus: {p['consensus']:.0%} agree on {p['signal']} in {p['regime']} regime ({p['total']} lessons)",
            "procedure": (
                f"1. Confirm {p['regime']} regime\n"
                f"2. Look for {p['signal']} setup\n"
                f"3. Target hold: {p['avg_held_hours']:.1f} hours\n"
                f"4. Crowd confidence: {p['avg_confidence']:.0f}/100"
            ),
            "pitfalls": f"Avoid when exit reason is '{p['top_exit_reason']}'. Symbols seen: {', '.join(p['symbols'][:5])}",
            "evidence": f"{p['total']} swarm lessons, {win_rate:.1f}% WR, consensus={p['consensus']:.0%}",
            "regime": p["regime"],
            "signal": p["signal"],
            "trade_count": p["total"],
            "win_rate": win_rate,
            "consensus": p["consensus"],
        }
        skills.append(skill)

    # Sort by consensus + trade count
    skills.sort(key=lambda s: s["trade_count"] * s["consensus"], reverse=True)
    return skills


def run_crowd_analysis(limit: int = 100) -> List[Dict]:
    """Full pipeline: pull lessons → analyze → extract skills → push proven ones."""
    hive = get_hivemind()
    if not hive.is_enabled():
        logger.info("[Crowd] HiveMind not enabled, skipping crowd analysis")
        return []

    logger.info(f"[Crowd] Pulling {limit} lessons from swarm...")
    lessons = hive.pull_lessons(limit=limit)

    if not lessons:
        logger.info("[Crowd] No lessons to analyze")
        return []

    logger.info(f"[Crowd] Analyzing {len(lessons)} lessons...")
    patterns = analyze_crowd(lessons)

    logger.info(f"[Crowd] Found {len(patterns)} patterns")
    skills = extract_skills(patterns)

    if not skills:
        logger.info("[Crowd] No skills met consensus threshold")
        return []

    pushed = []
    for skill in skills:
        try:
            ok = hive.push_skill(**skill)
            if ok:
                pushed.append(skill)
                logger.info(f"[Crowd] Pushed skill: {skill['name']} (n={skill['trade_count']}, wr={skill['win_rate']:.1f}%)")
        except Exception as e:
            logger.error(f"[Crowd] Failed to push skill {skill['name']}: {e}")

    logger.info(f"[Crowd] Analysis complete: {len(pushed)}/{len(skills)} skills pushed")
    return pushed
