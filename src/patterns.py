"""
Pattern Detection — Candlestick patterns for signal confirmation.
"""
import pandas as pd


def detect_candlestick_patterns(df: pd.DataFrame) -> list[dict]:
    """Detect candlestick patterns on last 5 candles. Returns list of pattern dicts."""
    if len(df) < 5:
        return []
    patterns = []
    closes = df["close"].values.tolist()
    opens = df["open"].values.tolist()
    highs = df["high"].values.tolist()
    lows = df["low"].values.tolist()
    last = {"open": opens[-1], "high": highs[-1], "low": lows[-1], "close": closes[-1]}
    prev = {"open": opens[-2], "high": highs[-2], "low": lows[-2], "close": closes[-2]}
    prev2 = {"open": opens[-3], "high": highs[-3], "low": lows[-3], "close": closes[-3]}
    if _check_doji(last):
        patterns.append({"name": "Doji", "confidence": 0.65, "direction": "neutral",
                         "description": "Indecision, potential reversal"})
    if _check_bullish_engulfing(last, prev):
        patterns.append({"name": "Bullish Engulfing", "confidence": 0.75, "direction": "bullish",
                         "description": "Strong bullish reversal signal"})
    if _check_bearish_engulfing(last, prev):
        patterns.append({"name": "Bearish Engulfing", "confidence": 0.75, "direction": "bearish",
                         "description": "Strong bearish reversal signal"})
    if _check_hammer(last):
        patterns.append({"name": "Hammer", "confidence": 0.70, "direction": "bullish",
                         "description": "Bottom rejection, potential bounce"})
    if _check_shooting_star(last):
        patterns.append({"name": "Shooting Star", "confidence": 0.70, "direction": "bearish",
                         "description": "Top rejection, potential drop"})
    if _check_morning_star(prev2, prev, last):
        patterns.append({"name": "Morning Star", "confidence": 0.80, "direction": "bullish",
                         "description": "Strong 3-candle bottom reversal"})
    if _check_evening_star(prev2, prev, last):
        patterns.append({"name": "Evening Star", "confidence": 0.80, "direction": "bearish",
                         "description": "Strong 3-candle top reversal"})
    if _check_bullish_marubozu(last):
        patterns.append({"name": "Bullish Marubozu", "confidence": 0.70, "direction": "bullish",
                         "description": "Strong buying pressure, no wicks"})
    if _check_bearish_marubozu(last):
        patterns.append({"name": "Bearish Marubozu", "confidence": 0.70, "direction": "bearish",
                         "description": "Strong selling pressure, no wicks"})
    return patterns


def get_candle_body(c: dict) -> float:
    return abs(c["close"] - c["open"])


def get_candle_range(c: dict) -> float:
    return c["high"] - c["low"]


def get_upper_shadow(c: dict) -> float:
    return c["high"] - max(c["open"], c["close"])


def get_lower_shadow(c: dict) -> float:
    return min(c["open"], c["close"]) - c["low"]


def _check_doji(c: dict) -> bool:
    rng = get_candle_range(c)
    if rng == 0:
        return False
    return get_candle_body(c) / rng < 0.15


def _check_bullish_engulfing(curr: dict, prev: dict) -> bool:
    prev_bearish = prev["close"] < prev["open"]
    curr_bullish = curr["close"] > curr["open"]
    if not (prev_bearish and curr_bullish):
        return False
    if curr["close"] <= prev["open"]:
        return False
    if curr["open"] > prev["close"]:
        return False
    return get_candle_body(curr) >= get_candle_body(prev)


def _check_bearish_engulfing(curr: dict, prev: dict) -> bool:
    prev_bullish = prev["close"] > prev["open"]
    curr_bearish = curr["close"] < curr["open"]
    if not (prev_bullish and curr_bearish):
        return False
    if curr["close"] >= prev["open"]:
        return False
    if curr["open"] < prev["close"]:
        return False
    return get_candle_body(curr) >= get_candle_body(prev)


def _check_hammer(c: dict) -> bool:
    rng = get_candle_range(c)
    body = get_candle_body(c)
    if rng == 0 or body == 0:
        return False
    lower = get_lower_shadow(c)
    upper = get_upper_shadow(c)
    if lower < body * 2.0:
        return False
    if upper > body * 0.5:
        return False
    mid = (c["open"] + c["close"]) / 2
    return mid > (c["high"] + c["low"]) / 2


def _check_shooting_star(c: dict) -> bool:
    rng = get_candle_range(c)
    body = get_candle_body(c)
    if rng == 0 or body == 0:
        return False
    upper = get_upper_shadow(c)
    lower = get_lower_shadow(c)
    if upper < body * 2.0:
        return False
    if lower > body * 0.5:
        return False
    mid = (c["open"] + c["close"]) / 2
    return mid < (c["high"] + c["low"]) / 2


def _check_morning_star(c1: dict, c2: dict, c3: dict) -> bool:
    if c1["close"] >= c1["open"]:
        return False
    if get_candle_body(c1) < get_candle_body(c2) * 2:
        return False
    if get_candle_body(c2) > get_candle_body(c1) * 0.4:
        return False
    if c3["close"] <= c3["open"]:
        return False
    c1_mid = (c1["open"] + c1["close"]) / 2
    return c3["close"] > c1_mid


def _check_evening_star(c1: dict, c2: dict, c3: dict) -> bool:
    if c1["close"] <= c1["open"]:
        return False
    if get_candle_body(c1) < get_candle_body(c2) * 2:
        return False
    if get_candle_body(c2) > get_candle_body(c1) * 0.4:
        return False
    if c3["close"] >= c3["open"]:
        return False
    c1_mid = (c1["open"] + c1["close"]) / 2
    return c3["close"] < c1_mid


def _check_bullish_marubozu(c: dict) -> bool:
    if c["close"] <= c["open"]:
        return False
    rng = get_candle_range(c)
    if rng == 0:
        return False
    body = get_candle_body(c)
    return body / rng > 0.85 and get_upper_shadow(c) < body * 0.1


def _check_bearish_marubozu(c: dict) -> bool:
    if c["close"] >= c["open"]:
        return False
    rng = get_candle_range(c)
    if rng == 0:
        return False
    body = get_candle_body(c)
    return body / rng > 0.85 and get_lower_shadow(c) < body * 0.1