"""
Position Sizing — Kelly-based adaptive sizing with fee adjustment.

Implements:
1. Quarter-Kelly fraction for risk-adjusted position sizing
2. Progress-aware risk scaling (conservative near targets)
3. Fee and slippage adjusted calculations
4. Minimum notional constraints
"""
import logging
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger(__name__)

BINANCE_MIN_NOTIONAL = {
    "BTCUSDT": 100, "ETHUSDT": 20, "SOLUSDT": 10,
    "BNBUSDT": 10, "DOGEUSDT": 5, "SHIBUSDT": 5,
    "XRPUSDT": 5, "ADAUSDT": 5, "DOTUSDT": 5,
    "AVAXUSDT": 5, "LINKUSDT": 5, "UNIUSDT": 5,
}

DEFAULT_MIN_NOTIONAL = 20

TAKER_FEE_RATE = 0.0005
MAKER_FEE_RATE = 0.0002
DEFAULT_SLIPPAGE_PCT = 0.001


@dataclass
class RiskTier:
    risk_pct: float
    max_leverage: int
    max_dd: float
    label: str


PROGRESS_TIERS = [
    RiskTier(risk_pct=0.05, max_leverage=10, max_dd=0.20, label="aggressive"),
    RiskTier(risk_pct=0.03, max_leverage=7,  max_dd=0.15, label="balanced"),
    RiskTier(risk_pct=0.02, max_leverage=5,  max_dd=0.10, label="conservative"),
    RiskTier(risk_pct=0.01, max_leverage=3,  max_dd=0.07, label="ultra_conservative"),
]

KELLY_FRACTION = 0.25


def get_risk_tier(current_capital: float, target_capital: float = 10000.0) -> RiskTier:
    if target_capital <= 0:
        return PROGRESS_TIERS[0]
    progress = current_capital / target_capital
    if progress < 0.10:
        return PROGRESS_TIERS[0]
    elif progress < 0.50:
        return PROGRESS_TIERS[1]
    elif progress < 0.80:
        return PROGRESS_TIERS[2]
    else:
        return PROGRESS_TIERS[3]


def kelly_fraction(win_rate: float, rr_ratio: float, fraction: float = KELLY_FRACTION) -> float:
    if rr_ratio <= 0 or win_rate <= 0:
        return 0.0
    full_kelly = win_rate - (1 - win_rate) / rr_ratio
    if full_kelly <= 0:
        return 0.0
    return min(full_kelly * fraction, 0.05)


def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    leverage: int,
    use_maker: bool = False,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
) -> Dict:
    if capital <= 0 or entry_price <= 0 or stop_loss_price <= 0 or leverage <= 0:
        return _empty_position()

    risk_amount = capital * risk_pct
    sl_distance = abs(entry_price - stop_loss_price) / entry_price

    if sl_distance <= 0:
        return _empty_position()

    notional = risk_amount / sl_distance
    fee_rate = MAKER_FEE_RATE if use_maker else TAKER_FEE_RATE
    fee_round_trip = notional * fee_rate * 2
    slippage_cost = notional * slippage_pct
    total_friction = fee_round_trip + slippage_cost
    risk_adjusted = risk_amount - total_friction

    if risk_adjusted <= 0:
        return _empty_position()

    contracts = notional / entry_price
    margin_required = notional / leverage
    fee_pct_of_risk = total_friction / risk_amount if risk_amount > 0 else 0

    return {
        "contracts": round(contracts, 6),
        "notional": round(notional, 2),
        "margin_required": round(margin_required, 2),
        "actual_risk_usd": round(risk_adjusted, 4),
        "fee_cost_usd": round(fee_round_trip, 4),
        "slippage_cost_usd": round(slippage_cost, 4),
        "total_friction_usd": round(total_friction, 4),
        "fee_pct_of_risk": round(fee_pct_of_risk, 4),
        "sl_distance_pct": round(sl_distance * 100, 2),
        "leverage": leverage,
    }


def calculate_adaptive_position_size(
    capital: float,
    target_capital: float,
    entry_price: float,
    stop_loss_price: float,
    win_rate: float,
    rr_ratio: float,
    pair: str = "",
    use_maker: bool = False,
) -> Dict:
    tier = get_risk_tier(capital, target_capital)

    kelly_size = kelly_fraction(win_rate, rr_ratio)
    effective_risk_pct = min(tier.risk_pct, max(kelly_size, tier.risk_pct * 0.5))

    min_notional = BINANCE_MIN_NOTIONAL.get(pair, DEFAULT_MIN_NOTIONAL)
    max_notional = capital * tier.max_leverage
    if max_notional * 0.3 < min_notional and pair:
        return {
            **_empty_position(),
            "tier": tier.label,
            "kelly_pct": round(kelly_size * 100, 2),
            "blocked": True,
            "block_reason": f"Notional ${max_notional * 0.3:.2f} < min ${min_notional} for {pair}",
        }

    result = calculate_position_size(
        capital=capital,
        risk_pct=effective_risk_pct,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        leverage=tier.max_leverage,
        use_maker=use_maker,
    )
    result["tier"] = tier.label
    result["tier_risk_pct"] = tier.risk_pct
    result["tier_max_leverage"] = tier.max_leverage
    result["tier_max_dd"] = tier.max_dd
    result["kelly_pct"] = round(kelly_size * 100, 2)
    result["effective_risk_pct"] = round(effective_risk_pct * 100, 2)
    result["blocked"] = False

    return result


def calculate_adaptive_leverage(
    symbol: str,
    capital: float,
    sl_pct: float,
    volatility: float,
    mode: str = "balanced",
) -> int:
    mode_config = {
        "aggressive":        {"base_leverage": 10, "multiplier": 1.0},
        "balanced":          {"base_leverage": 7,  "multiplier": 1.0},
        "conservative":      {"base_leverage": 5,  "multiplier": 1.0},
        "ultra_conservative": {"base_leverage": 3,  "multiplier": 1.0},
    }
    cfg = mode_config.get(mode, mode_config["balanced"])

    from src.binance_api import BinanceFuturesAPI
    api = BinanceFuturesAPI()
    pairs_info = api.get_tradeable_pairs_info(capital)
    pair_info = pairs_info.get(symbol, {})
    exchange_max_leverage = pair_info.get("max_leverage", 50)

    base_leverage = cfg["base_leverage"]

    if volatility > 10:
        base_leverage = min(base_leverage, 5)
    elif volatility > 7:
        base_leverage = min(base_leverage, 10)
    elif volatility > 4:
        base_leverage = min(base_leverage, 15)

    if capital < 50:
        base_leverage = int(base_leverage * 1.5)

    if sl_pct < 1.0:
        base_leverage = min(base_leverage, 10)
    elif sl_pct < 2.0:
        base_leverage = min(base_leverage, 15)

    leverage = min(base_leverage, exchange_max_leverage)
    leverage = max(1, leverage)

    logger.info(
        f"[PositionSizing] Adaptive leverage for {symbol}: {leverage}x "
        f"(mode={mode}, vol={volatility:.1f}%, cap=${capital:.0f}, "
        f"exchange_max={exchange_max_leverage}x)"
    )
    return int(leverage)


def _empty_position() -> Dict:
    return {
        "contracts": 0, "notional": 0, "margin_required": 0,
        "actual_risk_usd": 0, "fee_cost_usd": 0, "slippage_cost_usd": 0,
        "total_friction_usd": 0, "fee_pct_of_risk": 0, "sl_distance_pct": 0,
        "leverage": 0,
    }