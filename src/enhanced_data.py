"""
Enhanced Market Data Module — Advanced Futures Market Microstructure.

Fetches critical data for sophisticated signal generation:
1. Long/Short Ratio (sentiment)
2. Taker Buy/Sell Volume (order flow)
3. Order Book Depth (liquidity analysis)
4. Funding Rate History (carry cost trends)
5. Top Trader Long/Short Ratio (whale positioning)

All data is cached and rate-limited for efficient API usage.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import threading
import requests
import numpy as np
from src.binance_api import RateLimiter

logger = logging.getLogger(__name__)


class EnhancedFuturesData:

    """
    Enhanced data fetcher for Binance USDS-M Futures.
    
    Provides advanced market microstructure data beyond basic OHLCV.
    """
    
    BASE_URL = "https://fapi.binance.com"
    CACHE_TTL_SECONDS = 300  # 5 minutes cache
    
    def __init__(self, cache_dir: str = "data/enhanced_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "CoinScreener-Enhanced/1.0"
        })
        # Connection pooling
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
        self.session.mount("https://", adapter)
        self._cache: Dict[str, Tuple[any, float]] = {}  # In-memory cache
        self._lock = threading.Lock()
        self._rate_limiter = RateLimiter(calls=60, period=60)  # 60 calls/min for enhanced endpoints
        
    def _rate_limited_get(self, endpoint: str, params: Optional[dict] = None, 
                         timeout: int = 10) -> dict:
        """Make rate-limited GET request with token bucket limiter."""
        self._rate_limiter.acquire()  # block until token available
        url = f"{self.BASE_URL}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"[EnhancedData] API error for {endpoint}: {e}")
            return {}
    
    def _get_cached_or_fetch(self, key: str, fetch_func, ttl: int = None) -> any:
        """Get from cache or fetch if expired."""
        if ttl is None:
            ttl = self.CACHE_TTL_SECONDS
            
        with self._lock:
            if key in self._cache:
                data, timestamp = self._cache[key]
                if time.time() - timestamp < ttl:
                    return data
        
        # Fetch fresh data
        try:
            data = fetch_func()
            with self._lock:
                self._cache[key] = (data, time.time())
            return data
        except Exception as e:
            logger.error(f"[EnhancedData] Fetch error for {key}: {e}")
            # Return stale cache if available
            with self._lock:
                if key in self._cache:
                    return self._cache[key][0]
            return None
    
    # =========================================================================
    # 1. LONG/SHORT RATIO (Account-level sentiment)
    # =========================================================================
    
    def get_long_short_ratio(self, symbol: str, period: str = "15m") -> Optional[Dict]:
        """
        Get global long/short account ratio.
        
        Period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        
        Returns:
            {
                "longAccount": float,  # % accounts long
                "shortAccount": float, # % accounts short
                "longShortRatio": float,
                "timestamp": int
            }
        """
        cache_key = f"ls_ratio_{symbol}_{period}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period
            }
            data = self._rate_limited_get("/futures/data/globalLongShortAccountRatio", params)
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]  # Get most recent
                return {
                    "longAccount": float(latest.get("longAccount", 0)),
                    "shortAccount": float(latest.get("shortAccount", 0)),
                    "longShortRatio": float(latest.get("longShortRatio", 0)),
                    "timestamp": latest.get("timestamp", 0)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch)
    
    def get_long_short_ratio_trend(self, symbol: str, periods: int = 5) -> Optional[Dict]:
        """
        Get trend of L/S ratio over multiple periods.
        
        Returns trend direction and extremes for contrarian signals.
        """
        cache_key = f"ls_trend_{symbol}_{periods}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": "15m",
                "limit": periods
            }
            data = self._rate_limited_get("/futures/data/globalLongShortAccountRatio", params)
            if isinstance(data, list) and len(data) >= 3:
                long_accounts = [float(d.get("longAccount", 0)) for d in data]
                avg_long = sum(long_accounts) / len(long_accounts)
                latest_long = long_accounts[-1]
                
                # Determine trend
                if latest_long > avg_long * 1.05:
                    trend = "INCREASING_LONGS"
                elif latest_long < avg_long * 0.95:
                    trend = "DECREASING_LONGS"
                else:
                    trend = "STABLE"
                
                # Contrarian signals
                signal = None
                if latest_long > 0.75:
                    signal = "EXTREME_LONG_EXHAUSTION_RISK"  # Potential short
                elif latest_long < 0.30:
                    signal = "EXTREME_SHORT_SQUEEZE_RISK"    # Potential long
                    
                return {
                    "latest_long_pct": latest_long,
                    "avg_long_pct": avg_long,
                    "trend": trend,
                    "contrarian_signal": signal,
                    "data_points": len(data)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=180)  # 3 min cache
    
    # =========================================================================
    # 2. TAKER BUY/SELL VOLUME (Order flow direction)
    # =========================================================================
    
    def get_taker_volume_ratio(self, symbol: str, period: str = "15m") -> Optional[Dict]:
        """
        Get taker buy/sell volume ratio.
        
        Taker = Market orders (aggressive)
        Maker = Limit orders (passive)
        
        High taker buy = Aggressive buying (potential top)
        High taker sell = Aggressive selling (potential bottom)
        """
        cache_key = f"taker_{symbol}_{period}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period
            }
            data = self._rate_limited_get("/futures/data/takerlongshortRatio", params)
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                buy_vol = float(latest.get("buyVol", 0))
                sell_vol = float(latest.get("sellVol", 0))
                total = buy_vol + sell_vol
                
                if total > 0:
                    buy_pct = buy_vol / total
                    sell_pct = sell_vol / total
                    
                    # Signal interpretation
                    flow_signal = None
                    if buy_pct > 0.65:
                        flow_signal = "HEAVY_TAKER_BUYING"  # Distribution risk
                    elif sell_pct > 0.65:
                        flow_signal = "HEAVY_TAKER_SELLING" # Accumulation opportunity
                    
                    return {
                        "buyVolume": buy_vol,
                        "sellVolume": sell_vol,
                        "buyPct": round(buy_pct, 3),
                        "sellPct": round(sell_pct, 3),
                        "takerRatio": round(buy_vol / sell_vol, 3) if sell_vol > 0 else float('inf'),
                        "flowSignal": flow_signal,
                        "timestamp": latest.get("timestamp", 0)
                    }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch)
    
    # =========================================================================
    # 3. ORDER BOOK DEPTH (Liquidity analysis)
    # =========================================================================
    
    def get_order_book_depth(self, symbol: str, limit: int = 500) -> Optional[Dict]:
        """
        Get order book depth and analyze liquidity clusters with multi-depth weighting.
        
        Analyzes 0.1%, 0.5%, and 1.0% depths to detect spoofing vs real walls.
        """
        cache_key = f"ob_depth_{symbol}_{limit}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "limit": min(limit, 1000)
            }
            data = self._rate_limited_get("/fapi/v1/depth", params)
            if not data or "bids" not in data:
                return None
            
            bids = np.array([[float(p), float(q)] for p, q in data.get("bids", [])])
            asks = np.array([[float(p), float(q)] for p, q in data.get("asks", [])])
            
            if len(bids) == 0 or len(asks) == 0:
                return None
            
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            mid = (best_bid + best_ask) / 2
            
            # Multi-depth imbalance
            depths = [0.01, 0.05, 0.10] # 1.0%, 5.0%, 10%
            imbalances = {}
            
            for d in depths:
                bid_liq = bids[bids[:, 0] >= mid * (1 - d)][:, 1].sum()
                ask_liq = asks[asks[:, 0] <= mid * (1 + d)][:, 1].sum()
                ratio = bid_liq / ask_liq if ask_liq > 0 else 10.0
                imbalances[f"ratio_{int(d*1000)}bps"] = round(ratio, 2)
            
            # Weighted Imbalance (closer depth = higher weight)
            weighted_ratio = (imbalances["ratio_10bps"] * 0.5 + 
                             imbalances["ratio_50bps"] * 0.3 + 
                             imbalances["ratio_100bps"] * 0.2)
            
            return {
                "bestBid": best_bid,
                "bestAsk": best_ask,
                "spreadPct": round((best_ask - best_bid) / best_bid * 100, 4),
                "imbalances": imbalances,
                "weightedRatio": round(weighted_ratio, 2),
                "bias": "BULLISH" if weighted_ratio > 1.5 else "BEARISH" if weighted_ratio < 0.67 else "NEUTRAL"
            }
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=10) # Faster refresh for HFT

    
    # =========================================================================
    # 4. FUNDING RATE HISTORY (Carry cost trend)
    # =========================================================================
    
    def get_funding_rate_trend(self, symbol: str, periods: int = 24) -> Optional[Dict]:
        """
        Get funding rate trend and extreme readings.
        
        High positive funding = Longs pay shorts (overleveraged longs)
        High negative funding = Shorts pay longs (overleveraged shorts)
        """
        cache_key = f"funding_trend_{symbol}_{periods}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "limit": periods
            }
            data = self._rate_limited_get("/fapi/v1/fundingRate", params)
            if isinstance(data, list) and len(data) >= 3:
                rates = [float(d.get("fundingRate", 0)) for d in data]
                avg_rate = sum(rates) / len(rates)
                latest_rate = rates[-1]
                max_rate = max(rates)
                min_rate = min(rates)
                
                # Trend analysis
                if latest_rate > avg_rate * 1.5 and latest_rate > 0.0001:
                    trend = "INCREASING_PREMIUM"  # Longs getting more aggressive
                elif latest_rate < avg_rate * 0.5 and latest_rate < -0.0001:
                    trend = "INCREASING_DISCOUNT" # Shorts getting more aggressive
                else:
                    trend = "STABLE"
                
                # Extreme signals (annualized)
                annualized = latest_rate * 3 * 365  # 8h intervals
                extreme_signal = None
                if annualized > 30:  # >30% annualized
                    extreme_signal = "EXTREME_LONG_FUNDING"  # Contrarian short
                elif annualized < -30:
                    extreme_signal = "EXTREME_SHORT_FUNDING" # Contrarian long
                
                return {
                    "currentRate": latest_rate,
                    "currentRatePct": round(latest_rate * 100, 4),
                    "annualizedPct": round(annualized, 2),
                    "avgRate": round(avg_rate, 6),
                    "maxRate": round(max_rate, 6),
                    "minRate": round(min_rate, 6),
                    "trend": trend,
                    "extremeSignal": extreme_signal,
                    "dataPoints": len(data)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=600)  # 10 min cache
    
    # =========================================================================
    # 5. OPEN INTEREST HISTORY (Positioning trend)
    # =========================================================================
    
    def get_oi_change(self, symbol: str, hours: float) -> float:
        """
        Get real OI percentage change over N hours.
        Fetches OI history with appropriate period, compares by timestamp.

        Uses Binance period matching:
        - 1h:  period="5m",  limit=13 (65 min coverage)
        - 4h:  period="30m", limit=9  (4.5h coverage)
        - 24h: period="4h",  limit=7  (28h coverage)
        """
        if hours <= 1:
            period, limit = "5m", 13
        elif hours <= 4:
            period, limit = "30m", 9
        elif hours <= 24:
            period, limit = "4h", 7
        else:
            period, limit = "1d", max(2, int(hours / 24) + 2)

        cache_key = f"oi_change_{symbol}_{hours}h"

        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period,
                "limit": limit
            }
            data = self._rate_limited_get("/futures/data/openInterestHist", params)
            if not isinstance(data, list) or len(data) < 2:
                return 0.0

            latest = data[-1]
            latest_oi = float(latest.get("sumOpenInterestValue", 0))
            latest_ts = int(latest.get("timestamp", 0))

            if latest_oi <= 0:
                return 0.0

            target_ts = latest_ts - int(hours * 3600 * 1000)
            best = data[0]
            for d in data:
                d_ts = int(d.get("timestamp", 0))
                if abs(d_ts - target_ts) < abs(int(best.get("timestamp", 0)) - target_ts):
                    best = d

            past_oi = float(best.get("sumOpenInterestValue", 0))
            if past_oi <= 0:
                return 0.0

            return round((latest_oi - past_oi) / past_oi * 100, 2)

        result = self._get_cached_or_fetch(cache_key, fetch, ttl=120)
        return float(result) if result is not None else 0.0

    def get_open_interest_trend(self, symbol: str, period: str = "15m", 
                              limit: int = 20) -> Optional[Dict]:
        """
        Get open interest trend to detect positioning changes.
        
        OI + Price Up = Trend healthy (new money entering)
        OI + Price Down = Distribution (longs trapped)
        OI - Price Up = Short squeeze (weak shorts)
        OI - Price Down = Capitulation
        """
        cache_key = f"oi_trend_{symbol}_{period}_{limit}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period,
                "limit": limit
            }
            data = self._rate_limited_get("/futures/data/openInterestHist", params)
            if isinstance(data, list) and len(data) >= 5:
                oi_values = [float(d.get("sumOpenInterestValue", 0)) for d in data]
                latest_oi = oi_values[-1]
                avg_oi = sum(oi_values) / len(oi_values)
                
                # Trend
                if latest_oi > avg_oi * 1.05:
                    oi_trend = "INCREASING"
                elif latest_oi < avg_oi * 0.95:
                    oi_trend = "DECREASING"
                else:
                    oi_trend = "STABLE"
                
                return {
                    "latestOiValue": round(latest_oi, 2),
                    "avgOiValue": round(avg_oi, 2),
                    "oiTrend": oi_trend,
                    "oiChangePct": round((latest_oi / avg_oi - 1) * 100, 2),
                    "maxOi": round(max(oi_values), 2),
                    "minOi": round(min(oi_values), 2),
                    "dataPoints": len(data)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=300)
    
    # =========================================================================
    # 6. TOP TRADER LONG/SHORT RATIO (Whale positioning)
    # =========================================================================
    
    def get_top_trader_ratio(self, symbol: str, period: str = "15m") -> Optional[Dict]:
        """
        Get top trader (whale) positioning.
        
        Top traders often = smart money
        If whales long while retail short = bullish divergence
        """
        cache_key = f"top_trader_{symbol}_{period}"
        
        def fetch():
            params = {
                "symbol": symbol.upper(),
                "period": period
            }
            data = self._rate_limited_get("/futures/data/topLongShortAccountRatio", params)
            if isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                long_ratio = float(latest.get("longAccount", 0))
                short_ratio = float(latest.get("shortAccount", 0))
                
                return {
                    "longRatio": round(long_ratio, 3),
                    "shortRatio": round(short_ratio, 3),
                    "longShortRatio": float(latest.get("longShortRatio", 0)),
                    "whaleBias": "LONG" if long_ratio > 0.6 else "SHORT" if short_ratio > 0.6 else "NEUTRAL",
                    "timestamp": latest.get("timestamp", 0)
                }
            return None
        
        return self._get_cached_or_fetch(cache_key, fetch, ttl=300)
    
    # =========================================================================
    # 7. COMPOSITE DATA FETCHER (All-in-one for a symbol)
    # =========================================================================
    
    def get_enhanced_metrics(self, symbol: str) -> Dict:
        """
        Get all enhanced metrics for a symbol in one call.
        
        Returns comprehensive market microstructure analysis.
        """
        logger.debug(f"[EnhancedData] Fetching enhanced metrics for {symbol}")
        
        metrics = {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "longShortRatio": None,
            "takerVolume": None,
            "orderBook": None,
            "funding": None,
            "topTrader": None,
        }
        
        # Fetch all data types
        metrics["longShortRatio"] = self.get_long_short_ratio_trend(symbol)
        metrics["takerVolume"] = self.get_taker_volume_ratio(symbol)
        metrics["orderBook"] = self.get_order_book_depth(symbol)
        metrics["funding"] = self.get_funding_rate_trend(symbol)
        metrics["topTrader"] = self.get_top_trader_ratio(symbol)
        
        # Flatten metrics for Scorer & Dashboard compatibility (HFT Standard)
        taker_data = metrics["takerVolume"] or {}
        ob_data = metrics["orderBook"] or {}
        
        # CVD 1h: Derived from taker buy/sell ratio (positive = buy aggression)
        metrics["cvd_1h"] = round(taker_data.get("takerRatio", 1.0) - 1.0, 4)
        # OBI: Weighted order book ratio (bids vs asks)
        metrics["obi_01"] = round(ob_data.get("weightedRatio", 1.0), 2)
        # Map to expected scorer key
        metrics["orderbook_imbalance"] = round(ob_data.get("weightedRatio", 0.0), 4)
        
        # Volume MA ratio from taker data (buyPct as volume indicator)
        metrics["volume_ma_ratio"] = round(1 + (taker_data.get("buyPct", 0.5) - 0.5) * 4, 2) if taker_data else 1.0
        
        # Funding z-score: high positive = long-heavy funding, contrarian bearish signal
        funding_data = metrics.get("funding", {}) or {}
        if funding_data and funding_data.get("currentRate") is not None:
            rate = abs(float(funding_data.get("currentRate", 0)))
            if rate == 0:
                metrics["funding_z_val"] = 0.0
            elif rate > 0.001:
                metrics["funding_z_val"] = 3.0
            elif rate > 0.0005:
                metrics["funding_z_val"] = 2.0
            elif rate > 0.0001:
                metrics["funding_z_val"] = 1.0
            else:
                metrics["funding_z_val"] = 0.0
        else:
            metrics["funding_z_val"] = 0.0
        
        # Composite signals for dashboard microstructure tab
        signals = []
        if metrics["longShortRatio"] and metrics["longShortRatio"].get("contrarian_signal"):
            signals.append({
                "type": "CONTRARIAN",
                "signal": metrics["longShortRatio"]["contrarian_signal"],
                "strength": "HIGH" if metrics["longShortRatio"]["latest_long_pct"] > 0.75 else "MEDIUM"
            })
        if metrics["takerVolume"] and metrics["takerVolume"].get("flowSignal"):
            signals.append({
                "type": "ORDER_FLOW",
                "signal": metrics["takerVolume"]["flowSignal"],
                "strength": "HIGH" if metrics["takerVolume"]["buyPct"] > 0.7 or metrics["takerVolume"].get("sellPct", 0) > 0.7 else "MEDIUM"
            })
        if metrics["funding"] and metrics["funding"].get("extremeSignal"):
            signals.append({
                "type": "FUNDING",
                "signal": metrics["funding"]["extremeSignal"],
                "strength": "HIGH" if abs(metrics["funding"]["annualizedPct"]) > 50 else "MEDIUM"
            })
        if metrics["orderBook"] and metrics["orderBook"].get("imbalanceSignal"):
            signals.append({
                "type": "LIQUIDITY",
                "signal": metrics["orderBook"]["imbalanceSignal"],
                "strength": "HIGH" if metrics["orderBook"].get("liquidityRatio", 1) > 3 or metrics["orderBook"].get("liquidityRatio", 1) < 0.33 else "MEDIUM"
            })
        metrics["compositeSignals"] = signals

        # Aggregate sentiment score for dashboard display
        sentiment = 50
        if metrics["longShortRatio"]:
            sentiment += (0.5 - float(metrics["longShortRatio"].get("latest_long_pct", 0.5))) * 40
        if metrics["funding"]:
            sentiment -= float(metrics["funding"].get("annualizedPct", 0)) / 2
        if metrics["topTrader"]:
            sentiment += (float(metrics["topTrader"].get("longRatio", 0.5)) - 0.5) * 20
        metrics["sentimentScore"] = max(0, min(100, sentiment))
        metrics["sentiment"] = "BULLISH" if sentiment > 60 else "BEARISH" if sentiment < 40 else "NEUTRAL"

        return metrics
    
    def close(self):
        """Close HTTP session."""
        self.session.close()


# Singleton instance
_enhanced_data: Optional[EnhancedFuturesData] = None


def get_enhanced_data(cache_dir: str = "data/enhanced_cache") -> EnhancedFuturesData:
    """Get or create enhanced data fetcher singleton."""
    global _enhanced_data
    if _enhanced_data is None:
        _enhanced_data = EnhancedFuturesData(cache_dir)
    return _enhanced_data


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    data = get_enhanced_data()
    result = data.get_enhanced_metrics("BTCUSDT")
    print(json.dumps(result, indent=2))
