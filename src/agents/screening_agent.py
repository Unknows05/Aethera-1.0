"""
Screening Agent — Autonomous market screening with ReAct loop.
Scans 500+ coins, filters top candidates, runs Bull/Bear debate, outputs signals.
Runs every 15 minutes via scheduler.
"""
import asyncio
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime
from openai import OpenAI

logger = logging.getLogger(__name__)

SCREENING_SYSTEM_PROMPT = """You are the SCREENING AGENT in an autonomous crypto trading system.
Your role: scan the market, select the most promising pairs, and orchestrate deep analysis.

CAPABILITIES:
- Access to market data (klines, OI, funding, regime)
- Access to knowledge vault (past patterns, lessons, skills)
- Can query HiveMind swarm for crowd insights
- Can select symbols for Bull/Bear debate

PROCESS:
1. Read current market regime and vault context
2. Review quick-scan results for top 100 candidates
3. Select 5-15 pairs for deep analysis + debate
4. Output screening strategy with rationale

OUTPUT FORMAT (JSON only):
{
  "selected_pairs": ["BTCUSDT", "ETHUSDT", ...],
  "direction_bias": "BULL" | "BEAR" | "BOTH",
  "rationale": "why these pairs were selected",
  "market_context": "brief market summary",
  "vault_references": ["relevant skills or lessons"],
  "confidence_threshold": 65
}"""


class ScreeningAgent:
    """Autonomous screening agent with ReAct loop for symbol selection."""

    def __init__(self, model: str = "deepseek/deepseek-chat-v4:free",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str = ""):
        self.model = model
        self.client: Optional[OpenAI] = None
        if api_key:
            try:
                self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
            except Exception as e:
                logger.error(f"[ScreeningAgent] Connection failed: {e}")
        self._cycle_count = 0
        self._last_screening: Optional[str] = None
        self._screening_history: List[Dict] = []

    def is_ready(self) -> bool:
        return self.client is not None

    async def run_cycle(self, engine, data_collector, debate_orchestrator,
                        risk_gate, vault_search, vault_lessons, market_regime_data: Dict) -> Dict:
        """Run one full screening cycle. Returns {ok, summary, signals}."""
        self._cycle_count += 1
        cycle_start = datetime.now()
        logger.info(f"[ScreeningAgent] Cycle #{self._cycle_count} started")

        try:
            # Stage 0: Collect market-wide data
            regime = market_regime_data.get("regime", "SIDEWAYS")
            regime_conf = market_regime_data.get("confidence", 0.5)

            # Stage 1: Collect data for all symbols
            if data_collector and engine:
                symbols = engine.symbols if hasattr(engine, 'symbols') else []
                if symbols:
                    collected = await data_collector.collect_all(symbols)
                    logger.info(f"[ScreeningAgent] Collected data for {len(collected.get('symbols', {}))} symbols")
                else:
                    collected = {}

            # Stage 2: Generate candidate list via quick scoring
            candidates = await self._quick_score_symbols(engine, data_collector)

            # Stage 3: LLM strategist — select pairs for deep analysis
            if self.is_ready():
                selected = await self._llm_select_pairs(candidates, regime, regime_conf, vault_search)
            else:
                selected = candidates[:10]  # Fallback: top 10 by score

            # Stage 4: Run deep scan + debate for selected pairs
            debate_signals = []
            if debate_orchestrator and engine:
                vault_ctx = self._get_vault_context(vault_search, regime)
                for pair in selected[:15]:  # Max 15 debates per cycle
                    try:
                        symbol = pair.get("symbol", "")
                        if not symbol:
                            continue

                        debate_data = {
                            "klines_15m": pair.get("klines_15m", []),
                            "klines_1h": pair.get("klines_1h", []),
                            "klines_4h": pair.get("klines_4h", []),
                            "regime": regime,
                            "composite_score": pair.get("score", 50),
                            "oi_change_24h": pair.get("oi_change_24h", 0),
                            "funding_rate": pair.get("funding_rate", 0),
                            "btc_trend": pair.get("btc_trend", 0),
                        }

                        debate_result = await debate_orchestrator.run_debate(
                            symbol=symbol, data=debate_data, vault_context=vault_ctx)

                        signal = {
                            "symbol": symbol,
                            "signal": debate_result.get("signal", "WAIT"),
                            "confidence": debate_result.get("confidence", 50),
                            "bull_score": debate_result.get("bull_score", 50),
                            "bear_score": debate_result.get("bear_score", 50),
                            "reasons": debate_result.get("key_bull_points", []) + debate_result.get("key_bear_points", []),
                            "regime": regime,
                            "debate": debate_result,
                        }

                        # Risk gate check
                        if risk_gate:
                            portfolio_state = {}
                            try:
                                gate_result = risk_gate.check(signal, portfolio_state, regime)
                                signal["risk_gate"] = gate_result
                                if not gate_result.get("allowed"):
                                    signal["signal"] = "WAIT"
                                    signal["risk_gate_blocked"] = True
                            except Exception as e:
                                logger.debug(f"[ScreeningAgent] Risk gate: {e}")

                        debate_signals.append(signal)
                    except Exception as e:
                        logger.debug(f"[ScreeningAgent] Debate failed for {symbol}: {e}")

            # Store results
            self._last_screening = datetime.now().isoformat()
            self._screening_history.append({
                "cycle": self._cycle_count,
                "timestamp": self._last_screening,
                "regime": regime,
                "candidates": len(candidates),
                "debated": len(debate_signals),
                "signals_generated": sum(1 for s in debate_signals if s["signal"] != "WAIT"),
            })
            if len(self._screening_history) > 200:
                self._screening_history = self._screening_history[-200:]

            elapsed = (datetime.now() - cycle_start).total_seconds()
            logger.info(f"[ScreeningAgent] Cycle done in {elapsed:.1f}s — "
                       f"{len(debate_signals)} debated, "
                       f"{sum(1 for s in debate_signals if s['signal'] != 'WAIT')} signals")

            return {
                "ok": True,
                "cycle": self._cycle_count,
                "regime": regime,
                "summary": {
                    "candidates_scanned": len(candidates),
                    "pairs_debated": len(debate_signals),
                    "signals": sum(1 for s in debate_signals if s["signal"] != "WAIT"),
                    "elapsed_seconds": round(elapsed, 1),
                },
                "signals": debate_signals,
            }

        except Exception as e:
            logger.error(f"[ScreeningAgent] Cycle failed: {e}")
            return {"ok": False, "error": str(e), "cycle": self._cycle_count}

    async def _quick_score_symbols(self, engine, data_collector) -> List[Dict]:
        """Quick composite score for all symbols. Returns sorted list by score."""
        candidates = []
        symbols = engine.symbols if engine else []

        for symbol in symbols[:200]:  # Cap at 200 for performance
            try:
                # Use data from data collector if available
                sym_data = {}
                if data_collector and hasattr(data_collector, '_cache'):
                    sym_data = data_collector._cache.get(symbol, {})

                score = 50
                if sym_data:
                    klines = sym_data.get("klines_15m", [])
                    if klines and len(klines) >= 20:
                        closes = [float(k["close"]) for k in klines[-20:]]
                        ma20 = sum(closes) / len(closes)
                        current = closes[-1]
                        momentum = ((current - ma20) / ma20) * 100 if ma20 > 0 else 0
                        score = 50 + momentum * 2

                candidates.append({
                    "symbol": symbol,
                    "score": round(max(0, min(100, score)), 2),
                    "klines_15m": sym_data.get("klines_15m", []),
                    "klines_1h": sym_data.get("klines_1h", []),
                    "klines_4h": sym_data.get("klines_4h", []),
                    "oi_change_24h": data_collector._cache.get(symbol, {}).get("oi_change_24h", 0) if data_collector else 0,
                    "funding_rate": 0,
                    "btc_trend": 0,
                })
            except Exception:
                candidates.append({"symbol": symbol, "score": 50})

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    async def _llm_select_pairs(self, candidates: List[Dict], regime: str,
                                regime_conf: float, vault_search) -> List[Dict]:
        """Use LLM to select most promising pairs for deep analysis."""
        if not self.is_ready():
            return candidates[:10]

        top_candidates = candidates[:50]
        candidate_summary = "\n".join([
            f"  {c['symbol']}: score={c['score']:.1f}"
            for c in top_candidates[:20]
        ])

        vault_ctx = self._get_vault_context(vault_search, regime)

        prompt = f"""Market Regime: {regime} (confidence: {regime_conf:.0%})
Top Candidates (by composite score):
{candidate_summary}

{vault_ctx}

Select 5-15 pairs for deep analysis and Bull/Bear debate. Prioritize:
- High score pairs in favorable regime
- Pairs with vault history (patterns that worked before)
- Diversified across sectors/market caps

Output JSON only."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SCREENING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            result = json.loads(content)

            selected_pairs = result.get("selected_pairs", [])
            logger.info(f"[ScreeningAgent] LLM selected {len(selected_pairs)} pairs: {selected_pairs}")

            # Map back to candidate data
            selected = [c for c in candidates if c["symbol"] in selected_pairs]
            if not selected:
                selected = candidates[:10]
            return selected

        except Exception as e:
            logger.error(f"[ScreeningAgent] LLM selection failed: {e}")
            return candidates[:10]

    def _get_vault_context(self, vault_search, regime: str) -> str:
        """Get relevant vault knowledge for current regime."""
        vault_ctx = ""
        if vault_search:
            try:
                results = vault_search.search(f"{regime} regime pattern", limit=5)
                if results and results.get("results"):
                    vault_ctx = "Vault Knowledge:\n" + "\n".join([
                        f"  - {r.get('title', r.get('path', ''))}: {r.get('content', '')[:120]}"
                        for r in results["results"][:5]
                    ])
            except Exception:
                pass
        return vault_ctx

    def get_stats(self) -> Dict:
        """Return screening agent statistics."""
        return {
            "cycles": self._cycle_count,
            "last_screening": self._last_screening,
            "history": self._screening_history[-10:],
        }
