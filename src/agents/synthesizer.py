"""
Synthesizer — Aggregates Bull/Bear debate into final trading signal.
Neutral LLM role: weighs both arguments, outputs final decision.
"""
import json
import logging
from typing import Dict, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)

SYNTHESIZER_PROMPT = """You are a NEUTRAL SYNTHESIZER in a trading debate. Two agents have presented their cases:
- BULL AGENT argues for LONG
- BEAR AGENT argues for SHORT (or avoiding LONG)

Your job: weigh both arguments objectively and produce a final trading decision.

DECISION RULES:
- If bull confidence > bear confidence by 15+ → LONG
- If bear confidence > bull confidence by 15+ → SHORT
- If within 15 points → WAIT (no clear edge)
- Consider regime alignment (don't LONG in BEAR regime without strong evidence)
- Consider vault knowledge (past patterns matter)
- Be decisive — don't hedge

OUTPUT FORMAT (JSON only):
{
  "signal": "LONG" | "SHORT" | "WAIT",
  "confidence": 0-100,
  "bull_score": 0-100,
  "bear_score": 0-100,
  "decision_reason": "why this signal was chosen",
  "key_bull_points": ["top 2 bull arguments"],
  "key_bear_points": ["top 2 bear arguments"],
  "regime_alignment": "aligned" | "neutral" | "conflicting",
  "vault_influenced": true/false
}"""


class Synthesizer:
    """Aggregates debate arguments into final signal."""

    def __init__(self, model: str = "deepseek/deepseek-chat-v4:free",
                 base_url: str = "https://openrouter.ai/api/v1",
                 api_key: str = ""):
        self.model = model
        self.client: Optional[OpenAI] = None
        if api_key:
            try:
                self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
            except Exception as e:
                logger.error(f"[Synthesizer] Connection failed: {e}")

    def is_ready(self) -> bool:
        return self.client is not None

    def synthesize(self, bull_arg: Dict, bear_arg: Dict,
                   regime: str = "UNKNOWN") -> Dict:
        """Synthesize bull and bear arguments into final signal."""
        if not self.is_ready():
            return self._default_synthesize(bull_arg, bear_arg)

        user_prompt = f"""Bull Case:
{json.dumps(bull_arg, indent=2)}

Bear Case:
{json.dumps(bear_arg, indent=2)}

Regime: {regime}

Weigh both cases and produce final signal. JSON only."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYNTHESIZER_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=512,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or ""
            result = json.loads(content)
            return result

        except Exception as e:
            logger.error(f"[Synthesizer] Synthesis failed: {e}")
            return self._default_synthesize(bull_arg, bear_arg)

    def _default_synthesize(self, bull_arg: Dict, bear_arg: Dict) -> Dict:
        """Fallback: simple confidence comparison."""
        bull_conf = bull_arg.get("confidence", 50)
        bear_conf = bear_arg.get("confidence", 50)
        diff = bull_conf - bear_conf

        if diff > 15:
            signal = "LONG"
            confidence = bull_conf
        elif diff < -15:
            signal = "SHORT"
            confidence = bear_conf
        else:
            signal = "WAIT"
            confidence = max(bull_conf, bear_conf)

        return {
            "signal": signal,
            "confidence": confidence,
            "bull_score": bull_conf,
            "bear_score": bear_conf,
            "decision_reason": "Fallback synthesis (LLM unavailable)",
            "key_bull_points": bull_arg.get("arguments", [])[:2],
            "key_bear_points": bear_arg.get("arguments", [])[:2],
            "regime_alignment": "neutral",
            "vault_influenced": False,
        }
