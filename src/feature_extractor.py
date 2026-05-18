"""
Feature Extractor — 21 derivatives & technical features per signal for XGBoost ML.
Aligned with MLEngine.FEATURES (no zero-importance features).
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)


class FeatureExtractor:
    FEATURE_NAMES = [
        "oi_change_1h", "oi_change_4h", "oi_change_24h",
        "funding_rate",
        "ls_ratio_retail", "ls_ratio_top_trader", "taker_buy_ratio",
        "rsi_1h", "rsi_4h",
        "macd_histogram_4h", "macd_cross",
        "ema9_vs_ema21", "adx_4h", "bb_position", "atr_pct",
        "volume_zscore_1h", "volume_zscore_4h", "candle_aggression",
        "btc_trend_4h",
        "hours_since_last_signal",
    ]

    def __init__(self, db_path: str = None):
        self.db_path = db_path

    def extract_all(self, symbol: str, enhanced: Dict, klines_1h: List[Dict],
                    klines_4h: List[Dict], composite_score: float,
                    regime: str, db_hours_since: float = 0,
                    btc_klines_4h: List[Dict] = None) -> Dict:
        features = {"symbol": symbol, "signal_time": datetime.now().isoformat()}
        features.update(self._extract_derivatives(enhanced))
        features.update(self._extract_technicals(klines_1h, klines_4h))
        features.update(self._extract_market_context(klines_4h, regime, btc_klines_4h))
        features["hours_since_last_signal"] = db_hours_since
        return features

    def _extract_derivatives(self, enhanced: Dict) -> Dict:
        feats = {}
        feats["oi_change_1h"] = enhanced.get("oi_change_1h", 0) or 0
        feats["oi_change_4h"] = enhanced.get("oi_change_4h", 0) or 0
        feats["oi_change_24h"] = enhanced.get("oi_change_24h", 0) or 0
        funding = enhanced.get("funding", {}) or {}
        feats["funding_rate"] = funding.get("currentRate", 0) or 0
        ls_data = enhanced.get("longShortRatio", {}) or {}
        feats["ls_ratio_retail"] = ls_data.get("latest_long_pct", 0.5) or 0.5
        tt_data = enhanced.get("topTrader", {}) or {}
        feats["ls_ratio_top_trader"] = tt_data.get("longRatio", 0.5) or 0.5
        taker = enhanced.get("takerVolume", {}) or {}
        feats["taker_buy_ratio"] = taker.get("buyPct", 0.5) or 0.5
        return feats

    def _extract_technicals(self, klines_1h: List[Dict], klines_4h: List[Dict]) -> Dict:
        feats = {}
        feats["rsi_1h"] = self._calc_rsi(klines_1h, 14) if klines_1h else 50
        feats["rsi_4h"] = self._calc_rsi(klines_4h, 14) if klines_4h else 50
        if klines_4h and len(klines_4h) >= 26:
            macd_hist, macd_cross = self._calc_macd(klines_4h)
            feats["macd_histogram_4h"] = macd_hist
            feats["macd_cross"] = macd_cross
        else:
            feats["macd_histogram_4h"] = 0
            feats["macd_cross"] = 0
        feats["ema9_vs_ema21"] = self._calc_ema_diff(klines_4h) if klines_4h and len(klines_4h) >= 21 else 0
        feats["adx_4h"] = self._calc_adx(klines_4h, 14) if klines_4h and len(klines_4h) >= 14 else 25
        feats["bb_position"] = self._calc_bb_position(klines_4h) if klines_4h and len(klines_4h) >= 20 else 0.5
        feats["atr_pct"] = self._calc_atr_pct(klines_4h) if klines_4h and len(klines_4h) >= 14 else 0
        feats["volume_zscore_1h"] = self._calc_vol_zscore(klines_1h, 20) if klines_1h else 0
        feats["volume_zscore_4h"] = self._calc_vol_zscore(klines_4h, 20) if klines_4h else 0
        feats["candle_aggression"] = self._calc_candle_aggression(klines_4h[-1]) if klines_4h else 0
        return feats

    def _extract_market_context(self, klines_4h: List[Dict], regime: str,
                                 btc_klines_4h: List[Dict] = None) -> Dict:
        feats = {}
        if btc_klines_4h and len(btc_klines_4h) >= 21:
            ema9 = self._calc_ema(btc_klines_4h, 9)
            ema21 = self._calc_ema(btc_klines_4h, 21)
            if ema9 > ema21 * 1.01: feats["btc_trend_4h"] = 1
            elif ema9 < ema21 * 0.99: feats["btc_trend_4h"] = -1
            else: feats["btc_trend_4h"] = 0
        else:
            feats["btc_trend_4h"] = 0
        return feats

    # ── Indicator calculators ──────────────────────────────

    def _calc_rsi(self, klines: List[Dict], period: int = 14) -> float:
        if not klines or len(klines) < period + 1: return 50.0
        try:
            closes = pd.Series([float(k["close"]) for k in klines])
            delta = closes.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            avg_gain = gain.rolling(window=period, min_periods=period).mean()
            avg_loss = loss.rolling(window=period, min_periods=period).mean()
            last_avg_loss = avg_loss.iloc[-1]
            if last_avg_loss == 0: return 50.0 if avg_gain.iloc[-1] == 0 else 100.0
            rs = avg_gain / last_avg_loss
            rsi = 100 - (100 / (1 + rs))
            val = float(rsi.iloc[-1])
            return 50.0 if np.isnan(val) else val
        except Exception:
            return 50.0

    def _calc_macd(self, klines: List[Dict]) -> tuple:
        closes = pd.Series([float(k["close"]) for k in klines])
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line
        hist_val = float(histogram.iloc[-1])
        cross = 1 if macd_line.iloc[-1] > signal_line.iloc[-1] and macd_line.iloc[-2] <= signal_line.iloc[-2] else (
            -1 if macd_line.iloc[-1] < signal_line.iloc[-1] and macd_line.iloc[-2] >= signal_line.iloc[-2] else 0)
        return hist_val, cross

    def _calc_ema_diff(self, klines: List[Dict]) -> float:
        closes = pd.Series([float(k["close"]) for k in klines])
        ema9 = closes.ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = closes.ewm(span=21, adjust=False).mean().iloc[-1]
        last_close = closes.iloc[-1]
        return 0 if last_close == 0 else (ema9 - ema21) / last_close

    def _calc_adx(self, klines: List[Dict], period: int = 14) -> float:
        if len(klines) < period * 2: return 25.0
        try:
            df = pd.DataFrame(klines)
            high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
            plus_dm = high.diff().clip(lower=0)
            minus_dm = (-low.diff()).clip(lower=0)
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=period).mean()
            plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
            minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
            adx = dx.rolling(window=period).mean()
            return float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 25.0
        except Exception:
            return 25.0

    def _calc_bb_position(self, klines: List[Dict]) -> float:
        closes = pd.Series([float(k["close"]) for k in klines[-20:]])
        ma, std = closes.mean(), closes.std()
        if std == 0: return 0.5
        upper, lower = ma + 2 * std, ma - 2 * std
        price = closes.iloc[-1]
        return 0.5 if upper == lower else float(np.clip((price - lower) / (upper - lower), 0, 1))

    def _calc_atr_pct(self, klines: List[Dict], period: int = 14) -> float:
        if len(klines) < period: return 0.0
        try:
            df = pd.DataFrame(klines)
            high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=period).mean().iloc[-1]
            price = close.iloc[-1]
            return float(atr / price * 100) if price > 0 else 0.0
        except Exception:
            return 0.0

    def _calc_vol_zscore(self, klines: List[Dict], lookback: int = 20) -> float:
        if not klines or len(klines) < lookback: return 0.0
        try:
            volumes = pd.Series([float(k["volume"]) for k in klines])
            window = volumes.tail(lookback)
            mean, std = window.mean(), window.std()
            return 0.0 if std == 0 else float((volumes.iloc[-1] - mean) / std)
        except Exception:
            return 0.0

    def _calc_candle_aggression(self, candle: Dict) -> float:
        o, h, l, c = float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])
        body = abs(c - o)
        shadow = (h - l) - body
        if shadow <= 0: return 1.0 if body > 0 else 0.0
        return float(body / (body + shadow))

    def _calc_ema(self, klines: List[Dict], period: int) -> float:
        closes = pd.Series([float(k["close"]) for k in klines])
        return float(closes.ewm(span=period, adjust=False).mean().iloc[-1])
