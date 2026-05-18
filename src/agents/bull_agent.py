"""
Bull Agent — Generates LONG case arguments during debate.
Analyzes TA, OI, funding, regime, and vault knowledge for bullish evidence.
"""
import json
import logging
from typing import Dict, List, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)

BULL_SYSTEM_PROMPT = """You are the BULL AGENT in a trading debate. Your role is to build the strongest possible case for going LONG on the given symbol.

ARGUMENT RULES:
- Use ONLY evidence from the provided data (klines, OI, funding, regime, vault)
- Cite specific numbers (EMA values, volume ratios, OI changes)
- Reference vault knowledge if relevant (past winning patterns)
- Be specific and quantitative, not vague
- Acknowledge weak points but frame them positively

OUTPUT FORMAT (JSON only):
{
  "signal": "LONG",
  "confidence": 0-100,
  "arguments": ["specific bullish evidence 1", "specific bullish evidence 2", ...],
  "risks_acknowledged": ["risk 1", "risk 2"],
  "vault_references": ["skill or lesson name if referenced"],
  "reasoning": "concise summary of the bull case"
}"""


class BullAgent:
    """Generates LONG case arguments for debate."""

    def __init__(self, model: str = "deepseek/deepseek-chat-v4:free",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str = ""):
        self.model = model
        self.client: Optional[OpenAI] = None
        if api_key:
            try:
                self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
            except Exception as e:
                logger.error(f"[BullAgent] Connection failed: {e}")

    def is_ready(self) -> bool:
        return self.client is not None

    async def argue(self, symbol: str, data: Dict, vault_context: str = "") -> Dict:
        """Generate bull argument for the given symbol and data."""
        if not self.is_ready():
            return self._default_bull(symbol)

        user_prompt = self._build_prompt(symbol, data, vault_context)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": BULL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=512,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or ""
            result = json.loads(content)
            result["agent"] = "bull"
            return result

        except Exception as e:
            logger.error(f"[BullAgent] Argument failed: {e}")
            return self._default_bull(symbol)

    def _build_prompt(self, symbol: str, data: Dict, vault_context: str) -> str:
        """Build debate prompt with market data and vault context."""
        klines_15m = data.get("klines_15m", [])
        klines_1h = data.get("klines_1h", [])
        regime = data.get("regime", "UNKNOWN")
        score = data.get("composite_score", 50)

        # Extract key metrics
        price_info = ""
        if klines_15m and len(klines_15m) >= 20:
            closes = [float(k["close"]) for k in klines_15m[-20:]]
            ma20 = sum(closes) / len(closes)
            current = closes[-1]
            momentum = ((current - ma20) / ma20) * 100
            price_info = f"Price: {current:.4f} | MA20: {ma20:.4f} | Momentum: {momentum:+.2f}%"

        oi_info = data.get("oi_change_24h", "N/A")
        funding_info = data.get("funding_rate", "N/A")

        prompt = f"""Symbol: {symbol}
Regime: {regime}
Composite Score: {score}

Market Data:
{price_info}
OI Change 24h: {oi_info}
Funding Rate: {funding_info}

15m Klines (last 5): {json.dumps(klines_15m[-5:], default=str)[:500]}
1h Klines (last 3): {json.dumps(klines_1h[-3:], default=str)[:300]}
"""
        if vault_context:
            prompt += f"\nVault Knowledge:\n{vault_context}\n"

        prompt += "\nBuild your LONG case. Output JSON only."
        return prompt

    def _default_bull(self, symbol: str) -> Dict:
        """Fallback when LLM is unavailable."""
        return {
            "signal": "LONG",
            "confidence": 50,
            "arguments": ["Default bull case — LLM unavailable"],
            "risks_acknowledged": ["No LLM analysis"],
            "vault_references": [],
            "reasoning": "Fallback bull case",
            "agent": "bull",
        }
