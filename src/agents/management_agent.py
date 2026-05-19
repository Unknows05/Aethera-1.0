"""
Management Agent — Autonomous position monitoring and management.
Evaluates open positions every 5 minutes: STAY/CLOSE/MOVE_SL/ADD.
Uses LLM for tactical decisions, code-only for order execution.
"""
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime
from openai import OpenAI

logger = logging.getLogger(__name__)

MANAGEMENT_SYSTEM_PROMPT = """You are the MANAGEMENT AGENT in an autonomous crypto trading system.
Your role: monitor open positions and make tactical decisions to protect capital and maximize returns.

TOOLS AVAILABLE:
- get_positions() — current positions with PnL
- get_market_data(symbol) — live klines, OI, funding
- detect_regime() — has market regime changed?
- search_vault(query) — relevant lessons for this situation
- move_sl(symbol, new_price) — adjust stop loss
- close_position(symbol, reason) — close with reason

DECISION OPTIONS:
- STAY — position is healthy, no action needed
- MOVE_SL — trail stop loss to protect profit or reduce risk
- CLOSE — exit position (take profit, cut loss, regime change)
- ADD — add to winning position (if within risk limits)

RULES:
- Trail SL to breakeven when PnL > 2%
- Consider closing if regime changed against position
- Consider closing if funding turns highly unfavorable
- Never add more than max position size (5% of equity)
- Respect capital tier limits for leverage and trade count

OUTPUT FORMAT (JSON only):
{
  "decisions": [
    {
      "symbol": "BTCUSDT",
      "action": "STAY" | "MOVE_SL" | "CLOSE" | "ADD",
      "new_sl": null | number,
      "reason": "clear explanation",
      "pnl_pct": 2.5,
      "regime": "BULL"
    }
  ],
  "summary": "brief overview of all positions"
}"""


class ManagementAgent:
    """Monitors positions and generates STAY/CLOSE/MOVE_SL/ADD decisions."""

    def __init__(self, model: str = "deepseek/deepseek-chat-v4:free",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str = ""):
        self.model = model
        self.client: Optional[OpenAI] = None
        if api_key:
            try:
                self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
            except Exception as e:
                logger.error(f"[ManagementAgent] Connection failed: {e}")
        self._cycle_count = 0
        self._last_management: Optional[str] = None
        self._decision_history: List[Dict] = []

    def is_ready(self) -> bool:
        return self.client is not None

    async def manage(self, positions: List[Dict], prices: Dict[str, float],
                     regime: str, engine, vault_search) -> List[Dict]:
        """Evaluate all open positions and produce management decisions."""
        self._cycle_count += 1
        cycle_start = datetime.now()
        logger.info(f"[ManagementAgent] Cycle #{self._cycle_count} — {len(positions)} open positions")

        if not positions:
            return []

        try:
            # Get vault context for management
            vault_ctx = self._get_vault_context(vault_search)

            # Build enriched position data
            enriched = []
            for pos in positions:
                symbol = pos.get("symbol", "")
                entry = float(pos.get("entry_price", 0))
                current = prices.get(symbol, entry)
                pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
                direction = pos.get("direction", "LONG")
                if direction == "SHORT":
                    pnl_pct = -pnl_pct

                enriched.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry,
                    "current_price": current,
                    "pnl_pct": round(pnl_pct, 2),
                    "size_usd": float(pos.get("size_usd", 0)),
                    "sl_price": pos.get("sl_price"),
                    "tp_price": pos.get("tp_price"),
                    "leverage": pos.get("leverage", 1),
                    "funding_rate": pos.get("funding_rate", 0),
                })

            # LLM-based decision
            if self.is_ready():
                decisions = await self._llm_decide(enriched, regime, vault_ctx)
            else:
                decisions = self._deterministic_decide(enriched, regime)

            # Store history
            self._last_management = datetime.now().isoformat()
            self._decision_history.append({
                "cycle": self._cycle_count,
                "timestamp": self._last_management,
                "regime": regime,
                "positions": len(positions),
                "decisions": decisions,
            })
            if len(self._decision_history) > 200:
                self._decision_history = self._decision_history[-200:]

            elapsed = (datetime.now() - cycle_start).total_seconds()
            actions = [d for d in decisions if d["action"] != "STAY"]
            logger.info(f"[ManagementAgent] Cycle done in {elapsed:.1f}s — "
                       f"{len(actions)} actions needed")

            return decisions

        except Exception as e:
            logger.error(f"[ManagementAgent] Cycle failed: {e}")
            return []

    async def _llm_decide(self, positions: List[Dict], regime: str,
                          vault_context: str) -> List[Dict]:
        """Use LLM to make tactical management decisions."""
        if not self.is_ready():
            return self._deterministic_decide(positions, regime)

        pos_text = "\n".join([
            f"  {p['symbol']} {p['direction']} — Entry: {p['entry_price']:.4f}, "
            f"Current: {p['current_price']:.4f}, PnL: {p['pnl_pct']:+.2f}%, "
            f"SL: {p['sl_price']}, Leverage: {p['leverage']}x, "
            f"Funding: {p.get('funding_rate', 0):.4f}%"
            for p in positions
        ])

        prompt = f"""Market Regime: {regime}
Open Positions:
{pos_text}

{vault_context}

For each position, decide STAY/MOVE_SL/CLOSE/ADD.
- If PnL > 2%, trail SL to breakeven or better
- If regime changed against direction, tighten SL
- If funding highly unfavorable (>0.1% per 8h), consider closing
- If PnL < -3% and no recovery signal, CLOSE

Output JSON only."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": MANAGEMENT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            result = json.loads(content)
            return result.get("decisions", [])

        except Exception as e:
            logger.error(f"[ManagementAgent] LLM decision failed: {e}")
            return self._deterministic_decide(positions, regime)

    def _deterministic_decide(self, positions: List[Dict], regime: str) -> List[Dict]:
        """Fallback: deterministic rules when LLM unavailable."""
        decisions = []
        for pos in positions:
            symbol = pos["symbol"]
            pnl = pos["pnl_pct"]
            direction = pos["direction"]

            if pnl > 5:
                decisions.append({
                    "symbol": symbol, "action": "MOVE_SL",
                    "new_sl": pos["entry_price"],
                    "reason": f"PnL +{pnl:.1f}% — trail SL to breakeven",
                    "pnl_pct": pnl, "regime": regime,
                })
            elif pnl < -3:
                decisions.append({
                    "symbol": symbol, "action": "CLOSE",
                    "reason": f"PnL {pnl:.1f}% — stop loss triggered",
                    "pnl_pct": pnl, "regime": regime,
                })
            elif direction == "LONG" and regime == "BEAR":
                decisions.append({
                    "symbol": symbol, "action": "MOVE_SL",
                    "new_sl": pos["current_price"] * 0.98,
                    "reason": "LONG in BEAR regime — tighten SL",
                    "pnl_pct": pnl, "regime": regime,
                })
            elif direction == "SHORT" and regime == "BULL":
                decisions.append({
                    "symbol": symbol, "action": "MOVE_SL",
                    "new_sl": pos["current_price"] * 1.02,
                    "reason": "SHORT in BULL regime — tighten SL",
                    "pnl_pct": pnl, "regime": regime,
                })
            else:
                decisions.append({
                    "symbol": symbol, "action": "STAY",
                    "reason": "Position healthy, no action needed",
                    "pnl_pct": pnl, "regime": regime,
                })
        return decisions

    def _get_vault_context(self, vault_search) -> str:
        """Get relevant vault knowledge for position management."""
        vault_ctx = ""
        if vault_search:
            try:
                results = vault_search.search("position management loss", limit=3)
                if results and results.get("results"):
                    vault_ctx = "Vault Lessons:\n" + "\n".join([
                        f"  - {r.get('title', r.get('path', ''))}: {r.get('content', '')[:100]}"
                        for r in results["results"][:3]
                    ])
            except Exception:
                pass
        return vault_ctx

    def get_stats(self) -> Dict:
        """Return management agent statistics."""
        return {
            "cycles": self._cycle_count,
            "last_management": self._last_management,
            "recent_decisions": self._decision_history[-10:],
        }
