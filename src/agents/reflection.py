"""
Reflection Loop — Hourly learning, evolution, and swarm sync.
Analyzes closed positions, creates skills, evolves thresholds, syncs HiveMind.
"""
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime
from openai import OpenAI

logger = logging.getLogger(__name__)

REFLECTION_SYSTEM_PROMPT = """You are the REFLECTION AGENT in an autonomous crypto trading system.
Your role: analyze past trades, extract patterns, and create knowledge that improves future performance.

CAPABILITIES:
- Analyze closed trade outcomes (WIN/LOSS + patterns)
- Detect recurring patterns (3+ similar outcomes → skill)
- Create Markdown skill files with evidence and pitfalls
- Update bounded agent memory (MEMORY.md)
- Evolve thresholds based on rolling performance
- Push anonymized lessons to HiveMind swarm

OUTPUT FORMAT (JSON only):
{
  "analysis": {
    "trades_reviewed": 5,
    "wins": 3,
    "losses": 2,
    "avg_win_pnl": 3.2,
    "avg_loss_pnl": -1.5,
    "win_rate": 60.0
  },
  "patterns_detected": [
    {
      "name": "winning_bull_pattern",
      "regime": "BULL",
      "signal": "LONG",
      "occurrences": 4,
      "win_rate": 75.0,
      "evidence": "EMA golden cross + OI increasing",
      "pitfalls": ["Avoid when BTC dominance rises sharply"]
    }
  ],
  "skills_created": ["winning_bull_pattern"],
  "memory_updates": "Key lessons from this hour",
  "threshold_suggestions": {},
  "summary": "Brief hourly reflection"
}"""


class ReflectionAgent:
    """Hourly reflection: learn from outcomes, create skills, evolve, sync."""

    def __init__(self, model: str = "deepseek/deepseek-chat-v4:free",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str = ""):
        self.model = model
        self.client: Optional[OpenAI] = None
        if api_key:
            try:
                self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
            except Exception as e:
                logger.error(f"[ReflectionAgent] Connection failed: {e}")
        self._cycle_count = 0
        self._last_reflection: Optional[str] = None
        self._reflection_history: List[Dict] = []

    def is_ready(self) -> bool:
        return self.client is not None

    async def reflect(self, engine, vault_skills, vault_lessons, vault_memory,
                      vault_indexer, vault_search, hivemind_client) -> Dict:
        """Run one full reflection cycle. Returns {ok, analysis, skills_created}."""
        self._cycle_count += 1
        cycle_start = datetime.now()
        logger.info(f"[ReflectionAgent] Cycle #{self._cycle_count} started")

        try:
            # Step 1: Analyze closed trades from last period
            trades = self._get_closed_trades(engine)
            if not trades:
                logger.info("[ReflectionAgent] No closed trades to analyze")
                return self._empty_result()

            # Step 2: Run learning loop on engine
            if engine and hasattr(engine, '_run_learning_loop'):
                try:
                    signals = engine.get_signals().get("data", [])
                    await engine._run_learning_loop(signals)
                except Exception as e:
                    logger.debug(f"[ReflectionAgent] Learning loop: {e}")

            # Step 3: LLM reflection analysis
            analysis = {}
            if self.is_ready():
                analysis = await self._llm_analyze(trades, vault_search)
            else:
                analysis = self._deterministic_analyze(trades)

            # Step 4: Create skills from detected patterns
            skills_created = []
            if vault_skills and analysis.get("patterns_detected"):
                for pattern in analysis["patterns_detected"]:
                    try:
                        skill = vault_skills.create_skill(
                            name=pattern.get("name", "auto_pattern"),
                            regime=pattern.get("regime", "UNKNOWN"),
                            signal=pattern.get("signal", "LONG"),
                            evidence=pattern.get("evidence", ""),
                            pitfalls=pattern.get("pitfalls", []),
                            win_rate=pattern.get("win_rate", 0),
                            occurrences=pattern.get("occurrences", 0),
                        )
                        if skill:
                            skills_created.append(pattern["name"])
                            logger.info(f"[ReflectionAgent] Skill created: {pattern['name']}")
                    except Exception as e:
                        logger.debug(f"[ReflectionAgent] Skill creation failed: {e}")

            # Step 5: Create lessons from each trade
            if vault_lessons:
                for trade in trades:
                    try:
                        vault_lessons.create_lesson(
                            symbol=trade.get("symbol", "UNKNOWN"),
                            regime=trade.get("regime", "SIDEWAYS"),
                            signal=trade.get("signal", "WAIT"),
                            result=trade.get("result", "UNKNOWN"),
                            pnl_pct=trade.get("pnl_pct", 0),
                            reasons=trade.get("reasons", []),
                        )
                    except Exception as e:
                        logger.debug(f"[ReflectionAgent] Lesson creation: {e}")

            # Step 6: Update bounded memory
            if vault_memory and analysis.get("memory_updates"):
                try:
                    vault_memory.append(analysis["memory_updates"])
                except Exception as e:
                    logger.debug(f"[ReflectionAgent] Memory update: {e}")

            # Step 7: Evolve thresholds
            if engine and hasattr(engine, 'run_threshold_evolution'):
                try:
                    await engine.run_threshold_evolution(auto_apply=False)
                except Exception as e:
                    logger.debug(f"[ReflectionAgent] Threshold evolution: {e}")

            # Step 8: Re-index vault
            if vault_indexer:
                try:
                    vault_indexer.index_all()
                except Exception as e:
                    logger.debug(f"[ReflectionAgent] Vault re-index: {e}")

            # Step 9: Push anonymized lessons to HiveMind
            if hivemind_client and getattr(hivemind_client, 'is_enabled', lambda: False)():
                for trade in trades:
                    try:
                        hivemind_client.push_lesson(
                            symbol=trade.get("symbol", ""),
                            regime=trade.get("regime", "SIDEWAYS"),
                            signal=trade.get("signal", "WAIT"),
                            result=trade.get("result", "UNKNOWN"),
                            confidence=trade.get("confidence", 50),
                        )
                    except Exception as e:
                        logger.debug(f"[ReflectionAgent] HiveMind push: {e}")

            # Store history
            self._last_reflection = datetime.now().isoformat()
            self._reflection_history.append({
                "cycle": self._cycle_count,
                "timestamp": self._last_reflection,
                "trades_reviewed": len(trades),
                "skills_created": len(skills_created),
            })
            if len(self._reflection_history) > 100:
                self._reflection_history = self._reflection_history[-100:]

            elapsed = (datetime.now() - cycle_start).total_seconds()
            logger.info(f"[ReflectionAgent] Cycle done in {elapsed:.1f}s — "
                       f"{len(trades)} trades reviewed, {len(skills_created)} skills created")

            return {
                "ok": True,
                "cycle": self._cycle_count,
                "analysis": analysis.get("analysis", {}),
                "skills_created": skills_created,
                "trades_reviewed": len(trades),
                "elapsed_seconds": round(elapsed, 1),
            }

        except Exception as e:
            logger.error(f"[ReflectionAgent] Cycle failed: {e}")
            return {"ok": False, "error": str(e), "cycle": self._cycle_count}

    def _get_closed_trades(self, engine) -> List[Dict]:
        """Get recently closed trades from engine/DB."""
        trades = []
        try:
            if engine:
                # Try getting from DB with outcomes
                signals = engine.db.get_signals_with_outcomes(limit=50, result_filter=None, days=1)
                for s in (signals or []):
                    if s.get("outcome") in ("WIN", "LOSS"):
                        trades.append({
                            "symbol": s.get("symbol", ""),
                            "signal": s.get("signal", ""),
                            "regime": s.get("regime", "SIDEWAYS"),
                            "result": s.get("outcome", "UNKNOWN"),
                            "pnl_pct": s.get("pnl_pct", 0),
                            "confidence": s.get("confidence", 50),
                            "reasons": s.get("reasons", []),
                            "timestamp": s.get("timestamp", ""),
                        })
        except Exception as e:
            logger.debug(f"[ReflectionAgent] Trade fetch: {e}")
        return trades

    async def _llm_analyze(self, trades: List[Dict], vault_search) -> Dict:
        """Use LLM to analyze trades and detect patterns."""
        if not self.is_ready():
            return self._deterministic_analyze(trades)

        trade_text = "\n".join([
            f"  {t['symbol']} {t['signal']} — {t['result']} (PnL: {t.get('pnl_pct', 0):+.1f}%), "
            f"Regime: {t['regime']}, Conf: {t.get('confidence', 0)}%"
            for t in trades[:20]
        ])

        vault_ctx = ""
        if vault_search:
            try:
                results = vault_search.search("pattern", limit=3)
                if results and results.get("results"):
                    vault_ctx = "Existing Patterns:\n" + "\n".join([
                        f"  - {r.get('title', r.get('path', ''))}"
                        for r in results["results"][:3]
                    ])
            except Exception:
                pass

        prompt = f"""Recent Trades (last period):
{trade_text}

{vault_ctx}

Analyze trades and:
1. Compute performance stats (win rate, avg win/loss)
2. Detect recurring patterns (3+ similar outcomes)
3. Suggest new skills if pattern found
4. Update memory with key lessons

Output JSON only."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            return json.loads(content)

        except Exception as e:
            logger.error(f"[ReflectionAgent] LLM analysis failed: {e}")
            return self._deterministic_analyze(trades)

    def _deterministic_analyze(self, trades: List[Dict]) -> Dict:
        """Fallback: simple statistical analysis."""
        wins = [t for t in trades if t.get("result") == "WIN"]
        losses = [t for t in trades if t.get("result") == "LOSS"]

        avg_win = sum(t.get("pnl_pct", 0) for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.get("pnl_pct", 0) for t in losses) / len(losses) if losses else 0
        win_rate = (len(wins) / len(trades) * 100) if trades else 0

        # Simple pattern detection: group by regime+signal
        patterns = {}
        for t in trades:
            key = f"{t.get('regime','')}_{t.get('signal','')}"
            if key not in patterns:
                patterns[key] = {"wins": 0, "total": 0}
            patterns[key]["total"] += 1
            if t.get("result") == "WIN":
                patterns[key]["wins"] += 1

        detected = []
        for key, stats in patterns.items():
            if stats["total"] >= 3:
                wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
                parts = key.split("_")
                detected.append({
                    "name": f"{'winning' if wr >= 60 else 'losing'}_{parts[0].lower()}_pattern",
                    "regime": parts[0],
                    "signal": parts[1] if len(parts) > 1 else "LONG",
                    "occurrences": stats["total"],
                    "win_rate": round(wr, 1),
                    "evidence": f"Simple pattern {key}",
                    "pitfalls": [],
                })

        return {
            "analysis": {
                "trades_reviewed": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "avg_win_pnl": round(avg_win, 2),
                "avg_loss_pnl": round(avg_loss, 2),
                "win_rate": round(win_rate, 1),
            },
            "patterns_detected": detected,
            "skills_created": [p["name"] for p in detected],
            "memory_updates": f"Hour {self._cycle_count}: {len(wins)}W/{len(losses)}L, WR={win_rate:.0f}%",
            "threshold_suggestions": {},
            "summary": f"Reviewed {len(trades)} trades: {len(wins)} wins, {len(losses)} losses",
        }

    def _empty_result(self) -> Dict:
        return {
            "ok": True,
            "cycle": self._cycle_count,
            "analysis": {"trades_reviewed": 0, "wins": 0, "losses": 0},
            "skills_created": [],
            "trades_reviewed": 0,
        }

    def get_stats(self) -> Dict:
        """Return reflection agent statistics."""
        return {
            "cycles": self._cycle_count,
            "last_reflection": self._last_reflection,
            "history": self._reflection_history[-10:],
        }
