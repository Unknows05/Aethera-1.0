"""
Debate Orchestrator — Manages 3-round Bull/Bear debate flow.
Coordinates Bull Agent, Bear Agent, and Synthesizer.
"""
import asyncio
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DebateOrchestrator:
    """Orchestrates 3-round debate between Bull and Bear agents."""

    def __init__(self, bull_agent=None, bear_agent=None, synthesizer=None):
        self.bull = bull_agent
        self.bear = bear_agent
        self.synth = synthesizer
        self._debate_history: List[Dict] = []

    async def run_debate(self, symbol: str, data: Dict,
                         vault_context: str = "") -> Dict:
        """Run full 3-round debate. Returns final signal."""
        logger.info(f"[Debate] Starting debate for {symbol}")

        # Round 1: Opening arguments
        bull_r1 = await self.bull.argue(symbol, data, vault_context) if self.bull else {"confidence": 50, "arguments": []}
        bear_r1 = await self.bear.argue(symbol, data, vault_context, bull_argument=bull_r1) if self.bear else {"confidence": 50, "arguments": []}

        logger.info(f"[Debate] R1 — Bull: {bull_r1.get('confidence')}%, Bear: {bear_r1.get('confidence')}%")

        # Round 2: Rebuttals (agents respond to each other)
        bull_r2 = await self.bull.argue(symbol, data, vault_context) if self.bull else bull_r1
        bear_r2 = await self.bear.argue(symbol, data, vault_context, bull_argument=bull_r2) if self.bear else bear_r1

        logger.info(f"[Debate] R2 — Bull: {bull_r2.get('confidence')}%, Bear: {bear_r2.get('confidence')}%")

        # Round 3: Final arguments
        bull_r3 = await self.bull.argue(symbol, data, vault_context) if self.bull else bull_r2
        bear_r3 = await self.bear.argue(symbol, data, vault_context, bull_argument=bull_r3) if self.bear else bear_r2

        logger.info(f"[Debate] R3 — Bull: {bull_r3.get('confidence')}%, Bear: {bear_r3.get('confidence')}%")

        # Synthesize final decision
        result = self.synth.synthesize(bull_r3, bear_r3, data.get("regime", "UNKNOWN")) if self.synth else self._simple_decision(bull_r3, bear_r3)

        # Store debate history
        self._debate_history.append({
            "symbol": symbol,
            "bull_final": bull_r3.get("confidence", 50),
            "bear_final": bear_r3.get("confidence", 50),
            "result": result.get("signal", "WAIT"),
            "confidence": result.get("confidence", 50),
        })

        # Keep only last 100 debates
        if len(self._debate_history) > 100:
            self._debate_history = self._debate_history[-100:]

        logger.info(f"[Debate] Final — {symbol}: {result.get('signal')} ({result.get('confidence')}%)")
        return result

    def _simple_decision(self, bull_arg: Dict, bear_arg: Dict) -> Dict:
        """Simple fallback without synthesizer."""
        bull_conf = bull_arg.get("confidence", 50)
        bear_conf = bear_arg.get("confidence", 50)
        diff = bull_conf - bear_conf

        if diff > 15:
            signal, confidence = "LONG", bull_conf
        elif diff < -15:
            signal, confidence = "SHORT", bear_conf
        else:
            signal, confidence = "WAIT", max(bull_conf, bear_conf)

        return {
            "signal": signal,
            "confidence": confidence,
            "bull_score": bull_conf,
            "bear_score": bear_conf,
            "decision_reason": "Simple confidence comparison",
            "key_bull_points": bull_arg.get("arguments", [])[:2],
            "key_bear_points": bear_arg.get("arguments", [])[:2],
        }

    def get_debate_stats(self) -> Dict:
        """Return debate statistics."""
        if not self._debate_history:
            return {"total": 0}

        longs = sum(1 for d in self._debate_history if d["result"] == "LONG")
        shorts = sum(1 for d in self._debate_history if d["result"] == "SHORT")
        waits = sum(1 for d in self._debate_history if d["result"] == "WAIT")
        avg_conf = sum(d["confidence"] for d in self._debate_history) / len(self._debate_history)

        return {
            "total": len(self._debate_history),
            "longs": longs,
            "shorts": shorts,
            "waits": waits,
            "avg_confidence": round(avg_conf, 1),
        }
