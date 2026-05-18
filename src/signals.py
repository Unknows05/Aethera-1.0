"""
Signal Generator — LONG/SHORT/WAIT decision with multi-layer filtering.
ATR-based dynamic SL/TP per regime. Score-based primary, ML secondary.
"""
import pandas as pd
import logging

from src.utils import get_price_precision
from src.patterns import detect_candlestick_patterns

logger = logging.getLogger(__name__)

# Backtest-validated negative-PnL combos — HARD BLOCKED (absolute no-entry)
# These combos have proven net-negative PnL in backtesting
HARD_BLOCKED_COMBOS = {
    ('SIDEWAYS', 'LONG'): {
        'wr': 38.8, 
        'backtest_pnl': -15187, 
        'n': 1845,
        'reason': 'SIDEWAYS+LONG WR 38.8% — consistently unprofitable (net loss -$15,187)'
    },
    ('HIGH_VOL', 'SHORT'): {
        'wr': 40.0,
        'backtest_pnl': -5000,
        'n': 60,
        'reason': 'HIGH_VOL+SHORT WR 40.0% — volatility whipsaws cause losses'
    },
}

# Risky combos: confidence penalty + minimum confidence required (warning but not blocked)
RISKY_COMBOS = {
    ('BEAR', 'SHORT'):      {'penalty': 12, 'min_conf': 55, 'reason': '46.6% WR — below breakeven'},
    ('HIGH_VOL', 'SHORT'):  {'penalty': 15, 'min_conf': 60, 'reason': '40.0% WR — very high risk'},
}

# Regime-specific SL/TP multipliers (ATR-based)
REGIME_SL_TP = {
    "BULL": {"sl_mult": 2.5, "tp_mult": 3.5},
    "BEAR": {"sl_mult": 2.5, "tp_mult": 3.5},
    "SIDEWAYS": {"sl_mult": 1.5, "tp_mult": 2.5},
    "HIGH_VOL": {"sl_mult": 3.0, "tp_mult": 4.0},
}

DIRECTION_SL_TP = {
    ("SIDEWAYS", "SHORT"): {"sl_mult": 1.5, "tp_mult": 2.5},
    ("BULL", "LONG"):    {"sl_mult": 2.5, "tp_mult": 3.5},
    ("BULL", "SHORT"):   {"sl_mult": 2.0, "tp_mult": 3.0},
    ("BEAR", "SHORT"):   {"sl_mult": 2.0, "tp_mult": 3.5},
    ("HIGH_VOL", "LONG"):  {"sl_mult": 3.0, "tp_mult": 4.0},
    ("HIGH_VOL", "SHORT"): {"sl_mult": 3.0, "tp_mult": 4.0},
}


def _get_sl_tp_params(regime: str, signal_type: str) -> tuple:
    key = (regime, signal_type)
    if key in DIRECTION_SL_TP:
        cfg = DIRECTION_SL_TP[key]
        return cfg["sl_mult"], cfg["tp_mult"]
    regime_cfg = REGIME_SL_TP.get(regime, {"sl_mult": 1.5, "tp_mult": 3.0})
    return regime_cfg["sl_mult"], regime_cfg["tp_mult"]


def _calc_signal_levels(direction: int, entry: float, atr: float,
                         price_precision: int, regime: str, signal_type: str):
    sl_mult, tp_mult = _get_sl_tp_params(regime, signal_type)
    sl_offset = atr * sl_mult
    tp_offset = atr * tp_mult
    if direction == 1:
        return round(entry - sl_offset, price_precision), round(entry + tp_offset, price_precision)
    else:
        return round(entry + sl_offset, price_precision), round(entry - tp_offset, price_precision)


def _get_atr(klines: list[dict], period: int = 14) -> float:
    import numpy as np
    try:
        df = pd.DataFrame(klines)
        if len(df) == 0: return 0.0
        if len(df) < period: return float(df["close"].iloc[-1]) * 0.02
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.Series(np.maximum.reduce([tr1.values, tr2.values, tr3.values]), index=df.index)
        return float(tr.rolling(window=period).mean().iloc[-1])
    except Exception:
        return 0.0


def generate_signal(coin_data: dict, config: dict) -> dict:
    """
    Generate LONG/SHORT/WAIT signal from composite score with multi-layer filtering.
    Pipeline: Score Threshold → Hard Block → ML Filter → Microstructure → Confidence Adjust
    """
    score = coin_data.get("composite_score", 50)
    tf_metrics = coin_data.get("tf_metrics", {})
    tf_scores = coin_data.get("tf_scores", {})

    regime = coin_data.get("regime", {})
    regime_type = regime.get("regime", "SIDEWAYS") if isinstance(regime, dict) else str(regime)

    signal_config = config.get("signal", {})
    long_threshold = signal_config.get("long_min_score", 55)
    short_threshold = signal_config.get("short_min_score", 45)

    price = coin_data.get("price", 0)
    klines = coin_data.get("klines", [])
    atr = _get_atr(klines) if klines and price > 0 else price * 0.02
    if atr <= 0:
        atr = price * 0.02
    price_prec = get_price_precision(price)

    signal = "WAIT"
    confidence = 50
    entry = price
    sl = None
    tp = None
    reasons = []
    candle_signals = []

    # ── Step 0: Market Context Bias (max ±3) ──
    btc_trend = coin_data.get("btc_trend", 0)
    btc_dom_change = coin_data.get("btc_dom_change", 0)
    if btc_dom_change > 0.003:
        score -= 3
    elif btc_dom_change < -0.003:
        score += 3
    if btc_trend == -1 and btc_dom_change > 0:
        score -= 2
        reasons.append(f"Risk-off: BTC bearish + BTC.D rising")
    elif btc_trend == 1 and btc_dom_change < 0:
        score += 2
        reasons.append(f"Risk-on: BTC bullish + BTC.D falling")

    # SHORT bias di BEAR regime
    if regime_type == "BEAR":
        score -= 4
        reasons.append("BEAR regime: SHORT bias active")

    # ── Step 1: Determine signal direction from score ──
    if score >= long_threshold:
        predicted_direction = "LONG"
    elif score <= short_threshold:
        predicted_direction = "SHORT"
    else:
        predicted_direction = "WAIT"

    # ── Step 2: Hard block check ──
    if predicted_direction != "WAIT":
        block_key = (regime_type, predicted_direction)
        if block_key in HARD_BLOCKED_COMBOS:
            info = HARD_BLOCKED_COMBOS[block_key]
            return {
                "symbol": coin_data.get("symbol", ""), "price": price,
                "signal": "WAIT", "confidence": 30, "entry": price,
                "sl": None, "tp": None, "regime": regime_type,
                "composite_score": float(score), "score_breakdown": coin_data.get("score_breakdown", []),
                "reasons": [f"BLOCKED: {regime_type}+{predicted_direction} ({info['reason']})"],
                "tf_scores": tf_scores, "patterns_detected": [],
                "session": coin_data.get("session", "UNKNOWN"),
                "atr": 0, "atr_sl_mult": 0, "atr_tp_mult": 0,
                "ml_confidence": 0.0, "ml_passed": False, "enhanced": coin_data.get("enhanced", {}),
            }

    # ── Step 2.5: Risky combo penalty (penalty instead of hard block) ──
    if predicted_direction != "WAIT":
        risk_key = (regime_type, predicted_direction)
        if risk_key in RISKY_COMBOS:
            risky = RISKY_COMBOS[risk_key]
            score -= risky['penalty']
            reasons.append(f"Risky: {regime_type}+{predicted_direction} ({risky['reason']})")

    # ── Step 3: ML engine check ──
    ml_confidence = 50.0
    ml_passed = True
    ml = None
    try:
        from src.ml_engine import get_ml_engine
        ml = get_ml_engine()
        if ml.is_ready() and predicted_direction != "WAIT":
            features = coin_data.get("features", {})
            result = ml.filter_signal(features)
            ml_confidence = result.get("confidence", 50)
            ml_passed = result.get("pass", True)
    except Exception as e:
        logger.debug(f"ML engine error: {e}")

    # ── Step 4: Confidence calibration ──
    raw_conf = score
    calibrated_raw = raw_conf
    if raw_conf > 75:
        calibrated_raw = 71 + (raw_conf - 75) * 0.35
    elif raw_conf > 65:
        calibrated_raw = 65 + (raw_conf - 65) * 0.6

    if predicted_direction == "LONG":
        signal = "LONG"
        sl, tp = _calc_signal_levels(1, entry, atr, price_prec, regime_type, "LONG")
        tp = _adjust_tp_for_liquidations(signal, tp, coin_data.get("symbol"), price_prec)
        confidence = int(calibrated_raw)
        if regime_type == "SIDEWAYS":
            confidence = max(35, int(calibrated_raw) - 10)
            reasons.append("Caution: SIDEWAYS+LONG")
    elif predicted_direction == "SHORT":
        signal = "SHORT"
        if regime_type == "HIGH_VOL":
            confidence = max(40, int(calibrated_raw) - 10)
            reasons.append("Caution: HIGH_VOL SHORT")
        else:
            confidence = int(calibrated_raw)
        sl, tp = _calc_signal_levels(-1, entry, atr, price_prec, regime_type, "SHORT")
        tp = _adjust_tp_for_liquidations(signal, tp, coin_data.get("symbol"), price_prec)
    else:
        signal = "WAIT"
        confidence = max(40, int(calibrated_raw))

    # ── Step 5: ML confidence adjustment ──
    if signal in ("LONG", "SHORT") and ml and ml.is_ready():
        if ml_confidence < 25:
            signal = "WAIT"
            reasons.append(f"ML blocked: conf {ml_confidence:.0f}% extreme low")
        elif not ml_passed and ml_confidence < 40:
            confidence = max(35, confidence - 12)
            reasons.append(f"ML penalty: {ml_confidence:.0f}% below threshold")
        elif ml_passed and ml_confidence > 55:
            boost = min(8, int((ml_confidence - 50) * 0.3))
            confidence = min(85, confidence + boost)
            reasons.append(f"ML boost: +{boost} ({ml_confidence:.0f}%)")

    # ── Step 6: Coin-specific WR adjustment ──
    if signal in ("LONG", "SHORT"):
        try:
            from src.outcome_feedback import get_feedback
            fb = get_feedback()
            coin_wr_map = fb.load_coin_wr(days=30, min_trades=10)
            coin_wr_data = coin_wr_map.get(coin_data.get("symbol", ""))
            if coin_wr_data:
                coin_wr_val = coin_wr_data.win_rate / 100.0
                if coin_wr_val >= 0.65:
                    boost = min(5, int((coin_wr_val - 0.60) * 15))
                    confidence = min(85, confidence + boost)
                    reasons.append(f"Coin edge: {coin_wr_val:.0%} WR ({coin_wr_data.total}t)")
                elif coin_wr_val < 0.40 and coin_wr_data.total >= 20:
                    penalty = min(8, int((0.50 - coin_wr_val) * 15))
                    confidence = max(40, confidence - penalty)
                    reasons.append(f"Coin trap: {coin_wr_val:.0%} WR ({coin_wr_data.total}t)")
        except Exception as e:
            logger.debug(f"Coin WR adjustment error: {e}")

    confidence = max(30, min(85, confidence))

    # ── CONFIDENCE FLOOR: Block extremely weak signals ──
    if signal in ("LONG", "SHORT") and confidence < 35:
        signal = "WAIT"
        reasons.append(f"Extreme low confidence: {confidence}")

    # ── COIN MEMORY BLOCK ──
    if signal in ("LONG", "SHORT"):
        try:
            from src.coin_memory import recall_coin
            mem = recall_coin(coin_data.get("symbol", ""))
            if mem.get("total_trades", 0) >= 10 and mem.get("win_rate", 50) < 35:
                signal = "WAIT"
                reasons.append(f"Coin memory: {mem['symbol']} WR {mem['win_rate']}% ({mem['total_trades']}t) — blocked")
        except Exception:
            pass

    # ── VOLUME CONFIRMATION ──
    if signal in ("LONG", "SHORT"):
        enhanced_data = coin_data.get("enhanced", {})
        taker = enhanced_data.get("takerVolume", {}) or {}
        vol_ratio = float(enhanced_data.get("volume_ma_ratio", 1.0))
        if vol_ratio < 0.7:  # volume < 70% of average
            confidence = max(25, confidence - 10)
            reasons.append(f"Low volume: {vol_ratio:.1%} of avg")

    # ── Step 7: Market-wide dead zone ──
    if signal in ("LONG", "SHORT"):
        tf_metrics_local = coin_data.get("tf_metrics", {})
        session = coin_data.get("session", "")
        has_breakout = any(
            tf_metrics_local.get(tf, {}).get("breakout_bull")
            or tf_metrics_local.get(tf, {}).get("breakout_bear")
            for tf in ["15m", "1h", "4h"]
        )
        if not has_breakout and session in ("OFF-HOURS", "ASIA"):
            confidence = max(25, confidence - 8)
            reasons.append("Dead market: no breakout + low vol session")

    # ── Step 8: Microstructure filters ──
    if signal in ("LONG", "SHORT"):
        # Cascade liquidation block
        try:
            from src.liquidation import liquidation_heatmap
            heatmap_data = liquidation_heatmap.calculate_heatmap()
            heatmap = heatmap_data.get("heatmap", [])
            symbol_liq = next((item for item in heatmap if item["symbol"] == coin_data.get("symbol", "")), None)
            if symbol_liq and symbol_liq.get("intensity") == "high":
                if symbol_liq.get("total_value", 0) > 1_000_000:
                    signal = "WAIT"
                    reasons.append("BLOCKED: Cascade liquidation >$1M")
        except Exception:
            pass

        # Whale divergence
        enhanced_data_val = coin_data.get("enhanced", {})
        tt_data = enhanced_data_val.get("topTrader", {}) or {}
        ls_data = enhanced_data_val.get("longShortRatio", {}) or {}
        whale_long = float(tt_data.get("longRatio", 0.5)) if tt_data else 0.5
        retail_long = float(ls_data.get("latest_long_pct", 0.5)) if ls_data else 0.5
        if whale_long > 0.6 and retail_long < 0.45:
            confidence = max(40, confidence - 10)
            reasons.append("Whale divergence: whales buy, retail sell")
        elif whale_long < 0.4 and retail_long > 0.55:
            confidence = max(40, confidence - 10)
            reasons.append("Whale divergence: whales sell, retail buy")

        # Order book spread
        ob_data = enhanced_data_val.get("orderBook", {}) or {}
        spread = float(ob_data.get("spreadPct", 0)) if ob_data else 0
        if spread > 0.03:
            confidence = max(40, confidence - 5)
            reasons.append(f"OB wall: wide spread {spread:.2%}")

        # Counter-trend WR check
        try:
            from src.outcome_feedback import get_feedback
            fb = get_feedback()
            regime_wr_map = fb.load_regime_wr(days=7)
            key = (regime_type, signal)
            cwr = regime_wr_map.get(key)
            if cwr and cwr.total >= 10 and cwr.win_rate < 40:
                confidence = max(35, confidence - 8)
                reasons.append(f"Counter-trend: {regime_type}+{signal} WR {cwr.win_rate:.0f}%")
        except Exception:
            pass

        # Multi-TF confluence
        tf_metrics_local = coin_data.get("tf_metrics", {})
        bull_count = sum(1 for tf in ["15m", "1h", "4h"] if tf_metrics_local.get(tf, {}).get("breakout_bull"))
        bear_count = sum(1 for tf in ["15m", "1h", "4h"] if tf_metrics_local.get(tf, {}).get("breakout_bear"))
        if bull_count >= 2 and signal == "LONG":
            confidence = min(85, confidence + 5)
            reasons.append(f"Multi-TF confluence: {bull_count}/3 bullish")
        elif bear_count >= 2 and signal == "SHORT":
            confidence = min(85, confidence + 5)
            reasons.append(f"Multi-TF confluence: {bear_count}/3 bearish")
        elif signal == "LONG" and bear_count >= 2:
            confidence = max(40, confidence - 3)
            reasons.append("Multi-TF divergence")
        elif signal == "SHORT" and bull_count >= 2:
            confidence = max(40, confidence - 3)
            reasons.append("Multi-TF divergence")

    # ── Step 9: Candle pattern confirmation ──
    market_filter_cfg = config.get("market_filter", {})
    candle_cfg = market_filter_cfg.get("candle_confirm", {})
    candle_enabled = candle_cfg.get("enabled", True)
    candle_penalty = candle_cfg.get("confidence_penalty", 10)
    contrarian_penalty = candle_cfg.get("contrarian_penalty", 15)
    if candle_enabled and signal in ("LONG", "SHORT") and klines and len(klines) >= 5:
        df = pd.DataFrame(klines[-20:])
        candle_signals = detect_candlestick_patterns(df)
        bullish = [p for p in candle_signals if p["direction"] == "bullish"]
        bearish = [p for p in candle_signals if p["direction"] == "bearish"]
        if signal == "LONG" and not bullish and bearish:
            confidence = max(40, confidence - contrarian_penalty)
            reasons.append(f"Candle: {bearish[0]['name']} (contrarian)")
        elif signal == "LONG" and not bullish:
            confidence = max(40, confidence - candle_penalty)
        elif signal == "SHORT" and not bearish and bullish:
            confidence = max(40, confidence - contrarian_penalty)
            reasons.append(f"Candle: {bullish[0]['name']} (contrarian)")
        elif signal == "SHORT" and not bearish:
            confidence = max(40, confidence - candle_penalty)

    # ── Step 10: Funding rate filter ──
    funding_cfg = market_filter_cfg.get("funding", {})
    funding_max = funding_cfg.get("max_rate_pct", 0.05)
    enhanced_data = coin_data.get("enhanced", {})
    funding_info = enhanced_data.get("funding", {})
    if funding_info and isinstance(funding_info, dict):
        funding_rate = funding_info.get("currentRate", 0)
        if signal == "LONG" and funding_rate > (funding_max / 100):
            confidence = max(40, confidence - 10)
            reasons.append(f"Funding: {funding_rate*100:.3f}% (crowded long)")
        elif signal == "SHORT" and funding_rate < -0.0003:
            confidence = max(40, confidence - 8)
            reasons.append(f"Funding: {funding_rate*100:.3f}% (crowded short)")

    # ── Step 11: Minimum SL distance (0.5% of price) ──
    if sl and entry:
        min_sl_distance = price * 0.005
        current_sl_dist = abs(entry - sl)
        if current_sl_dist < min_sl_distance:
            if signal == "LONG":
                sl = round(entry - min_sl_distance, price_prec)
            elif signal == "SHORT":
                sl = round(entry + min_sl_distance, price_prec)

    # ── Step 12: Reason collection ──
    m15 = tf_metrics.get("15m", {})
    breakout_bull = m15.get("breakout_bull", False)
    breakout_bear = m15.get("breakout_bear", False)
    if breakout_bull and signal == "LONG":
        reasons.append("Bullish Breakout")
    elif breakout_bear and signal == "SHORT":
        reasons.append("Bearish Breakout")
    for p in candle_signals[:2]:
        reasons.append(f"Candle: {p['name']}")
    if score >= 75: reasons.append("Strong Bullish Momentum")
    elif score >= 60: reasons.append("Bullish Momentum")
    elif score <= 25: reasons.append("Strong Bearish Momentum")
    elif score <= 40: reasons.append("Bearish Momentum")
    if signal != "WAIT":
        if regime_type == "BULL" and signal == "LONG": reasons.append("Bullish Trend Regime")
        elif regime_type == "BEAR" and signal == "SHORT": reasons.append("Bearish Trend Regime")
    vol_z = m15.get("vol_z", 0)
    if vol_z > 2.0: reasons.append("High Volume Spike")
    elif vol_z < -1.0: reasons.append("Low Volume")
    rsi = m15.get("rsi", 50)
    if rsi > 70: reasons.append("Overbought (RSI)")
    elif rsi < 30: reasons.append("Oversold (RSI)")

    session_name = coin_data.get("session", "UNKNOWN")

    return {
        "symbol": coin_data.get("symbol", ""),
        "price": price,
        "signal": signal,
        "confidence": int(confidence),
        "entry": entry, "sl": sl, "tp": tp,
        "regime": regime_type,
        "composite_score": float(score),
        "score_breakdown": coin_data.get("score_breakdown", []),
        "reasons": reasons,
        "tf_scores": tf_scores,
        "patterns_detected": coin_data.get("patterns_detected", []),
        "session": session_name,
        "atr": round(atr, 6),
        "atr_sl_mult": _get_sl_tp_params(regime_type, signal)[0] if signal in ("LONG", "SHORT") else 1.5,
        "atr_tp_mult": _get_sl_tp_params(regime_type, signal)[1] if signal in ("LONG", "SHORT") else 3.0,
        "ml_confidence": round(ml_confidence, 1),
        "ml_passed": ml_passed,
        "enhanced": coin_data.get("enhanced", {}),
    }


def _adjust_tp_for_liquidations(signal: str, current_tp: float, symbol: str, precision: int) -> float:
    try:
        from src.liquidation import liquidation_heatmap
        heatmap_data = liquidation_heatmap.calculate_heatmap()
        heatmap = heatmap_data.get("heatmap", [])
        symbol_liq = next((item for item in heatmap if item["symbol"] == symbol), None)
        if not symbol_liq or symbol_liq.get("intensity") != "high":
            return current_tp
        if signal == "LONG":
            return round(current_tp * 1.005, precision)
        return round(current_tp * 0.995, precision)
    except Exception:
        return current_tp
