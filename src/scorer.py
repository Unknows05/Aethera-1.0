"""
Scoring Engine — 8-factor weighted scoring with adaptive regime detection.
Regime detection now uses ADX + price-vs-MA + volatility for balanced signals.
"""
import logging
from typing import Dict, Optional
import numpy as np

from src.config_loader import get_config

logger = logging.getLogger(__name__)


def _default_weights():
    cfg = get_config()
    return cfg.get('factor_weights', {}) or {
        'momentum': 0.15, 'mean_reversion': 0.15,
        'volume': 0.15, 'orderflow': 0.15,
        'funding': 0.10, 'breakout': 0.10,
        'sentiment': 0.10, 'pattern': 0.10,
        'smart_money': 0.10,
    }


class Scorer:
    def __init__(self, config=None):
        self.config = config or {}
        self.BASE_FACTOR_WEIGHTS = _default_weights()
        self.DEFAULT_WEIGHTS = self.BASE_FACTOR_WEIGHTS.copy()
        logger.info("[Scorer] Initialized")

    def calculate(self, data: Dict, regime: str = None) -> Dict:
        if regime is None:
            regime = self.detect_regime(data)
        scores = {}
        for name, method in [
            ('momentum', self._momentum_score),
            ('mean_reversion', self._mean_reversion_score),
            ('volume', self._volume_score),
            ('orderflow', self._orderflow_score),
            ('funding', self._funding_score),
            ('breakout', self._breakout_score),
            ('sentiment', self._sentiment_score),
            ('pattern', self._pattern_score),
            ('smart_money', self._smart_money_score),
        ]:
            try:
                scores[name] = method(data)
            except Exception as e:
                logger.debug(f"{name} error: {e}")
                scores[name] = 50

        weights = self._get_adaptive_weights(regime)
        composite = 50.0
        breakdown = []
        factor_names = {
            'momentum': 'Trend Momentum',
            'mean_reversion': 'Mean Reversion',
            'volume': 'Volume Anomaly',
            'orderflow': 'Orderbook Imbalance',
            'funding': 'Funding Bias',
            'breakout': 'Volatility Breakout',
            'sentiment': 'Whales CVD',
            'pattern': 'Chart Pattern',
            'smart_money': 'Smart Money Flow',
        }
        for factor in scores:
            impact = (scores[factor] - 50) * weights.get(factor, 0)
            composite += impact
            if abs(impact) >= 1.0:
                sign = "+" if impact > 0 else ""
                breakdown.append(f"{factor_names.get(factor, factor)}: {sign}{impact:.1f}%")
        breakdown.sort(key=lambda x: abs(float(x.split(': ')[1].replace('%', ''))), reverse=True)
        composite = np.clip(composite, 0, 100)
        return {
            'composite_score': round(composite, 2),
            'regime': regime,
            'raw_scores': scores,
            'weights': weights,
            'score_breakdown': breakdown[:4],
        }

    def detect_regime(self, data: Dict, btc_trend: int = 0) -> str:
        """
        Multi-signal regime detection with BTC anchor.
        Priority: HIGH_VOL > BULL > BEAR > SIDEWAYS
        btc_trend: 1 = BTC bullish, -1 = BTC bearish, 0 = neutral
        """
        spread = data.get('spread_pct', 0)
        vol_z = data.get('vol_z_val', 0)
        if vol_z > 2.0 or spread > 0.05:
            return 'HIGH_VOL'

        price = float(data.get('close', 0))
        ma20 = float(data.get('ma20', price))
        ma50 = float(data.get('ma50', ma20))

        # BTC anchor: if BTC is bearish, bias everything toward BEAR
        if btc_trend == -1 and price < ma20:
            return 'BEAR'
        if btc_trend == 1 and price > ma20:
            return 'BULL'

        if ma20 > 0 and ma50 > 0:
            trend_strength = (price - ma20) / ma20
            ma_cross = (ma20 - ma50) / ma50
            if trend_strength > 0.005 and ma_cross > 0.002:
                return 'BULL'
            if trend_strength < -0.005 and ma_cross < -0.002:
                return 'BEAR'

        vol_ratio = float(data.get('volume_ma_ratio', 1.0))
        if vol_ratio > 1.5:
            return 'BULL'
        if vol_ratio < 0.6:
            return 'BEAR'

        fz = float(data.get('funding_z_val', 0))
        if fz > 2.5:
            return 'HIGH_VOL'

        return 'SIDEWAYS'

    def _get_adaptive_weights(self, regime: str) -> Dict[str, float]:
        profiles = {
            'BULL': {'momentum': 1.4, 'volume': 1.2, 'funding': 0.8, 'orderflow': 1.2,
                     'breakout': 1.1, 'mean_reversion': 0.7, 'pattern': 0.9, 'smart_money': 0.9},
            'BEAR': {'mean_reversion': 2.5, 'volume': 1.3, 'funding': 1.5, 'orderflow': 1.1,
                     'breakout': 0.6, 'momentum': 0.3, 'pattern': 0.9, 'smart_money': 1.3},
            'SIDEWAYS': {'mean_reversion': 1.5, 'pattern': 1.4, 'volume': 0.9, 'momentum': 0.8,
                         'funding': 1.0, 'breakout': 0.9, 'orderflow': 0.9, 'smart_money': 0.7},
            'HIGH_VOL': {'momentum': 0.7, 'volume': 1.5, 'funding': 1.3, 'breakout': 1.0,
                          'mean_reversion': 0.7, 'pattern': 0.8, 'smart_money': 0.8},
        }
        base = self.DEFAULT_WEIGHTS.copy()
        profile = profiles.get(regime, profiles['SIDEWAYS'])
        for k in base:
            base[k] = base[k] * profile.get(k, 1.0)
        total = sum(base.values())
        return {k: round(v / total, 4) for k, v in base.items()}

    # ── Individual factor scores ────────────────────────────

    def _momentum_score(self, data: Dict) -> float:
        price = float(data.get('close', 0))
        ma20 = float(data.get('ma20', price))
        ma50 = float(data.get('ma50', ma20))
        if ma50 <= 0 or price == ma20 == ma50:
            return 50
        mom = (price - ma50) / ma50 * 100
        return float(np.clip((mom + 50) * 1.5, 0, 100))

    def _mean_reversion_score(self, data: Dict) -> float:
        price = float(data.get('close', 0))
        bb_mid = float(data.get('bb_mid', price))
        if bb_mid <= 0:
            return 50
        ratio = (price - bb_mid) / (bb_mid or 1)
        return float(np.clip(50 - ratio * 100, 0, 100))

    def _volume_score(self, data: Dict) -> float:
        vol = float(data.get('volume_ma_ratio', 1))
        if vol == 1.0:
            return 50
        if vol > 2.0: return 80 + min((vol - 2) * 10, 20)
        elif vol < 0.5: return 80 + min((0.5 - vol) * 100, 20)
        else: return 50 + (vol - 0.5) * 100

    def _orderflow_score(self, data: Dict) -> float:
        imb = float(data.get('orderbook_imbalance', 0))
        if imb == 0:
            return 50
        if imb > 0.2: return 80 + min((imb - 0.2) * 500, 20)
        elif imb < -0.2: return 80 + min((-(imb + 0.2)) * 500, 20)
        else: return 50 + (1 - abs(imb)) * 50

    def _funding_score(self, data: Dict) -> float:
        fz = float(data.get('funding_z_val', 0))
        if fz == 0:
            return 50
        if fz > 2.5: return 20
        elif fz < -2.5: return 80
        else: return 50 + (2.5 - abs(fz)) * 12

    def _breakout_score(self, data: Dict) -> float:
        price = float(data.get('close', 0))
        upper = float(data.get('bb_upper', price))
        lower = float(data.get('bb_lower', price))
        if upper == lower: return 50
        range_pct = (upper - lower) / lower
        position = (price - lower) / (upper - lower) if range_pct > 0 else 0.5
        if position > 0.85 or position < 0.15: return 85
        else: return 50 + min(position * 100, 50)

    def _sentiment_score(self, data: Dict) -> float:
        tt = data.get('topTrader', {})
        if not tt: return 50
        long_ratio = float(tt.get('longRatio', 0.5))
        return float(np.clip(50 + (long_ratio - 0.5) * 150, 0, 100))

    def _pattern_score(self, data: Dict) -> float:
        price = float(data.get('close', 0))
        upper = float(data.get('bb_upper', price))
        lower = float(data.get('bb_lower', price))
        vol_ratio = float(data.get('volume_ma_ratio', 1.0))
        if upper == lower or price <= 0:
            return 50
        bb_pos = (price - lower) / (upper - lower)
        volume_confirm = min(max((vol_ratio - 1) * 15, -10), 10)
        base = bb_pos * 100
        return float(np.clip(base + volume_confirm, 0, 100))

    def _smart_money_score(self, data: Dict) -> float:
        tt = data.get('topTrader', {})
        ls = data.get('longShortRatio', {})
        whale = float(tt.get('longRatio', 0.5)) if tt else 0.5
        retail = float(ls.get('latest_long_pct', 0.5)) / 100 if ls else 0.5
        funding = float(data.get('funding_z_val', 0))
        oi_change = float(data.get('oi_change_pct', 0))

        score = 50

        if oi_change > 10 and funding < 0:
            score -= 20
        elif oi_change > 10 and funding > 1:
            score += 10
        elif oi_change < -10 and funding > 1:
            score += 20

        gap = whale - retail
        if gap < -0.2 and funding > 1:
            score -= 15
        elif gap > 0.2 and funding < 0:
            score += 15

        fv = float(data.get('funding_velocity', 0))
        if fv > 5:
            score += 10
        elif fv < -5:
            score -= 10

        return float(np.clip(score, 0, 100))

    def _get_rsi(self, klines: list[dict], period: int = 14) -> float:
        if not klines or len(klines) < period + 1:
            return 50.0
        try:
            import pandas as pd
            df = pd.DataFrame(klines)
            close = df["close"].astype(float)
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return float(rsi.iloc[-1])
        except Exception:
            return 50.0
