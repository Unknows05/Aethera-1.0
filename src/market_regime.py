"""
Market-Wide Regime Detector — detects broad market regime using BTC as anchor
plus aggregate market conditions (volume, volatility, funding, OI).
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """Detects market-wide regime using BTC dominance, trend, volatility, and market breadth."""

    def __init__(self, api=None):
        self.api = api
        self._btc_klines: Optional[List[Dict]] = None
        self._market_data: Dict = {}
        self._last_regime: str = "SIDEWAYS"
        self._regime_confidence: float = 0.0

    def fetch_btc_data(self) -> bool:
        """Fetch BTC klines for regime analysis."""
        if not self.api:
            return False
        try:
            self._btc_klines = self.api.get_klines("BTCUSDT", "4h", 100)
            return bool(self._btc_klines)
        except Exception as e:
            logger.error(f"[MarketRegime] BTC fetch failed: {e}")
            return False

    def fetch_market_data(self, tickers: List[Dict] = None) -> Dict:
        """Fetch aggregate market metrics from 24h tickers."""
        if not tickers and self.api:
            try:
                tickers = self.api.get_24h_ticker()
            except Exception:
                tickers = []

        if not tickers:
            return self._market_data

        usdt_pairs = [t for t in tickers if t.get("symbol", "").endswith("USDT")]
        if not usdt_pairs:
            return self._market_data

        # Market breadth: % of coins above/below MA20
        above_ma = 0
        below_ma = 0
        total_vol = 0
        avg_change = 0
        high_vol_count = 0

        for t in usdt_pairs:
            try:
                change = float(t.get("priceChangePercent", 0))
                vol = float(t.get("quoteVolume", 0))
                avg_change += change
                total_vol += vol
                if change > 0:
                    above_ma += 1
                else:
                    below_ma += 1
                if abs(change) > 10:
                    high_vol_count += 1
            except Exception:
                continue

        total = above_ma + below_ma
        self._market_data = {
            "breadth_pct": (above_ma / total * 100) if total > 0 else 50,
            "avg_change": avg_change / max(len(usdt_pairs), 1),
            "total_volume": total_vol,
            "high_volatility_coins": high_vol_count,
            "coins_analyzed": len(usdt_pairs),
        }
        return self._market_data

    def detect_regime(self) -> Dict:
        """Detect market-wide regime using BTC + aggregate data."""
        regime = "SIDEWAYS"
        confidence = 0.5
        signals = []

        # BTC trend analysis
        btc_trend = 0
        if self._btc_klines and len(self._btc_klines) >= 50:
            closes = [float(k["close"]) for k in self._btc_klines]
            ema9 = self._ema(closes[-9:])
            ema21 = self._ema(closes[-21:])
            ema50 = self._ema(closes[-50:])

            # EMA alignment
            if ema9 > ema21 > ema50:
                btc_trend = 1
                signals.append("BTC_EMA_BULLISH")
            elif ema9 < ema21 < ema50:
                btc_trend = -1
                signals.append("BTC_EMA_BEARISH")

            # Volatility (ATR-like)
            recent_closes = closes[-20:]
            returns = [(recent_closes[i] - recent_closes[i-1]) / recent_closes[i-1]
                       for i in range(1, len(recent_closes))]
            avg_return = sum(returns) / len(returns)
            volatility = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5

            if volatility > 0.03:
                signals.append("HIGH_VOLATILITY")
            elif volatility < 0.01:
                signals.append("LOW_VOLATILITY")
        else:
            signals.append("NO_BTC_DATA")

        # Market breadth
        breadth = self._market_data.get("breadth_pct", 50)
        avg_change = self._market_data.get("avg_change", 0)
        high_vol = self._market_data.get("high_volatility_coins", 0)

        if breadth > 70 and btc_trend > 0:
            regime = "BULL"
            confidence = min(0.95, 0.5 + (breadth - 50) / 100)
            signals.append("BROAD_RALLY")
        elif breadth < 30 and btc_trend < 0:
            regime = "BEAR"
            confidence = min(0.95, 0.5 + (50 - breadth) / 100)
            signals.append("BROAD_SELL_OFF")
        elif high_vol > len(self._market_data.get("coins_analyzed", 1)) * 0.3:
            regime = "HIGH_VOL"
            confidence = 0.6
            signals.append("HIGH_VOL_CLUSTER")
        elif abs(avg_change) < 1 and 40 < breadth < 60:
            regime = "SIDEWAYS"
            confidence = 0.7
            signals.append("CONSOLIDATION")
        elif breadth > 60 and btc_trend == 0:
            regime = "ALT_SEASON"
            confidence = 0.6
            signals.append("ALTS_OUTPERFORMING")
        elif breadth < 40 and btc_trend == 0:
            regime = "RISK_OFF"
            confidence = 0.6
            signals.append("ALTS_UNDERPERFORMING")

        self._last_regime = regime
        self._regime_confidence = confidence

        return {
            "regime": regime,
            "confidence": round(confidence, 2),
            "btc_trend": btc_trend,
            "breadth_pct": round(breadth, 1),
            "avg_change_pct": round(avg_change, 2),
            "signals": signals,
            "timestamp": datetime.now().isoformat(),
        }

    def get_regime_bias(self, coin_regime: str) -> str:
        """Adjust coin-level signal based on market-wide regime."""
        market = self._last_regime
        if market == "BULL":
            return "LONG_BIAS"
        elif market == "BEAR":
            return "SHORT_BIAS"
        elif market in ("HIGH_VOL", "RISK_OFF"):
            return "CAUTION"
        elif market == "ALT_SEASON":
            return "ALT_BIAS"
        elif market == "SIDEWAYS":
            return "NEUTRAL"
        return "NEUTRAL"

    @staticmethod
    def _ema(values: List[float]) -> float:
        """Calculate EMA from a list of values."""
        multiplier = 2 / (len(values) + 1)
        ema = values[0]
        for v in values[1:]:
            ema = (v - ema) * multiplier + ema
        return ema


_market_regime: Optional[MarketRegimeDetector] = None


def get_market_regime(api=None) -> MarketRegimeDetector:
    global _market_regime
    if _market_regime is None:
        _market_regime = MarketRegimeDetector(api=api)
    return _market_regime
