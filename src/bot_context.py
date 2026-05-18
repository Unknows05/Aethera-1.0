"""
Bot Context — builds capability context string for LLM strategist prompt.
"""
import logging
from typing import Dict, List

from src.binance_api import BinanceFuturesAPI
from src.session_filter import SessionFilter

logger = logging.getLogger(__name__)

LEVERAGE_RULES = {
    "low_volatility":  {"max_vol": 4,  "max_leverage": 15, "description": "Low vol (<4%): leverage up to 15x"},
    "medium_volatility": {"max_vol": 7,  "max_leverage": 10, "description": "Medium vol (4-7%): leverage up to 10x"},
    "high_volatility": {"max_vol": 10, "max_leverage": 5,  "description": "High vol (7-10%): leverage up to 5x"},
    "extreme_volatility": {"max_vol": 999, "max_leverage": 3, "description": "Extreme vol (>10%): leverage capped at 3x"},
}

SESSION_PROFILES = {
    "Asian":   {"hours": "00:00-09:00 UTC", "bias": "RANGE",   "volatility": "Low-Medium", "notes": "Low vol, breakout fakeouts common"},
    "London":  {"hours": "09:00-16:00 UTC", "bias": "TREND",   "volatility": "Medium-High", "notes": "Volume spike, trend starts"},
    "NY":      {"hours": "16:00-00:00 UTC", "bias": "MOMENTUM", "volatility": "High",        "notes": "Highest vol, reversals at US close"},
}


def format_top_pairs(pairs: Dict[str, Dict], limit: int = 20) -> str:
    top = list(pairs.items())[:limit]
    lines = []
    for symbol, info in top:
        status = "TRADEABLE" if info.get("can_trade") else "BLOCKED"
        lines.append(
            f"  {symbol}: lev={info.get('max_leverage')}x, "
            f"min_notional=${info.get('min_notional')}, tick={info.get('tick_size')}, "
            f"status={status}"
        )
    return "\n".join(lines)


def build_capability_context(capital: float) -> str:
    api = BinanceFuturesAPI()
    pairs = api.get_tradeable_pairs_info(capital)

    total = len(pairs)
    tradeable = {s: v for s, v in pairs.items() if v.get("can_trade")}
    tradeable_count = len(tradeable)

    lines: List[str] = []
    lines.append("═══ CAPABILITY CONTEXT ═══")
    lines.append(f"Capital: ${capital:,.2f}")
    lines.append(f"Total PERPETUAL USDT-M pairs: {total}")
    lines.append(f"Tradeable pairs (capital sufficient): {tradeable_count}")

    lines.append("")
    lines.append("─── Leverage Rules by Volatility ───")
    for tier_label, tier in LEVERAGE_RULES.items():
        lines.append(f"  {tier['description']}")

    lines.append("")
    lines.append("─── Session Profile ───")
    for session_name, profile in SESSION_PROFILES.items():
        lines.append(f"  {session_name} ({profile['hours']}): bias={profile['bias']}, "
                     f"vol={profile['volatility']}, notes={profile['notes']}")

    lines.append("")
    lines.append(f"─── Top 20 Pairs (by listing order) ───")
    lines.append(format_top_pairs(tradeable, limit=20))

    # Summary guidance
    lines.append("")
    lines.append("─── Strategist Guidance ───")
    lines.append("  - Max concurrent trades: 3-5 (based on available capital)")
    lines.append("  - Per-trade risk: 2-5% of capital (conservative) to 5-8% (aggressive)")
    lines.append("  - Avoid pairs where max_leverage * capital * 0.3 < min_notional")
    lines.append("  - Prefer pairs in top 20 by volume for liquidity")
    lines.append("  - Session bias: ASIAN=fade breakouts, LONDON=trend follow, NY=momentum scalps")
    lines.append("")
    lines.append("─── Smart Money Analytics ───")
    lines.append("LLM can query: get_smart_money_flow(symbol) → funding rate, OI, whale vs retail positioning")

    return "\n".join(lines)
