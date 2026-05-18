"""
Data Collector — L0: Parallel data fetch for all market data.
No LLM, just efficient API calls with rate limit awareness.
"""
import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class DataCollector:
    """Collects market data from Binance in parallel with rate limit guards."""

    def __init__(self, api=None):
        self.api = api
        self._cache: Dict[str, Dict] = {}
        self._cache_time: Dict[str, float] = {}
        self._last_fetch: float = 0

    async def collect_all(self, symbols: List[str]) -> Dict:
        """Collect all data for given symbols in parallel. Returns dict of symbol → data."""
        if not self.api:
            return {}

        results = {}

        # Stage 1: 24h ticker for ALL symbols (1 API call)
        try:
            loop = asyncio.get_event_loop()
            tickers = await loop.run_in_executor(None, self.api.get_24h_ticker)
            ticker_map = {t["symbol"]: t for t in tickers if t["symbol"] in symbols}
            results["tickers"] = ticker_map
        except Exception as e:
            logger.error(f"[DataCollector] Ticker fetch failed: {e}")
            results["tickers"] = {}

        # Stage 2: BTC klines for regime (1 API call)
        try:
            loop = asyncio.get_event_loop()
            btc_klines = await loop.run_in_executor(None, self.api.get_klines, "BTCUSDT", "4h", 100)
            results["btc_klines"] = btc_klines or []
        except Exception as e:
            logger.error(f"[DataCollector] BTC klines failed: {e}")
            results["btc_klines"] = []

        # Stage 3: Per-symbol data (batched to respect rate limits)
        per_symbol = {}
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            tasks = [self._collect_symbol(s) for s in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for sym, result in zip(batch, batch_results):
                if isinstance(result, dict):
                    per_symbol[sym] = result

        results["symbols"] = per_symbol
        self._last_fetch = datetime.now().timestamp()
        return results

    async def _collect_symbol(self, symbol: str) -> Dict:
        """Collect deep data for a single symbol."""
        data = {"symbol": symbol, "ok": False}
        try:
            loop = asyncio.get_event_loop()

            # Parallel fetch: klines + enhanced data + OI
            klines_15m = loop.run_in_executor(None, self.api.get_klines, symbol, "15m", 100)
            klines_1h = loop.run_in_executor(None, self.api.get_klines, symbol, "1h", 100)
            klines_4h = loop.run_in_executor(None, self.api.get_klines, symbol, "4h", 100)

            results = await asyncio.gather(klines_15m, klines_1h, klines_4h, return_exceptions=True)

            data["klines_15m"] = results[0] if not isinstance(results[0], Exception) else []
            data["klines_1h"] = results[1] if not isinstance(results[1], Exception) else []
            data["klines_4h"] = results[2] if not isinstance(results[2], Exception) else []
            data["ok"] = bool(data["klines_15m"])

        except Exception as e:
            logger.debug(f"[DataCollector] {symbol} failed: {e}")

        return data

    async def collect_funding_oi(self, symbols: List[str]) -> Dict:
        """Collect funding rates and OI for symbols."""
        results = {}
        for symbol in symbols[:20]:  # Limit to avoid rate limits
            try:
                loop = asyncio.get_event_loop()
                funding = await loop.run_in_executor(None, self.api.get_funding_rate, symbol, 3)
                results[symbol] = {"funding": funding or []}
            except Exception:
                results[symbol] = {"funding": []}
        return results

    def get_cache_age(self) -> float:
        """Return seconds since last full collection."""
        if self._last_fetch == 0:
            return float('inf')
        return datetime.now().timestamp() - self._last_fetch

    def is_fresh(self, max_age_seconds: int = 60) -> bool:
        """Check if cached data is still fresh."""
        return self.get_cache_age() < max_age_seconds
