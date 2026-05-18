"""
Dynamic Universe Selector — rotates monitored symbols based on volume, volatility,
and blacklist. Replaces static symbol list with top-N dynamic selection.
"""
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

UNIVERSE_CACHE_PATH = "data/universe_cache.json"
BLACKLIST_PATH = "data/blacklist.json"

# Default blacklist: leveraged tokens, unstable coins, known issues
DEFAULT_BLACKLIST = {
    # Leveraged tokens
    "BTCDOWNUSDT", "BTCUPUSDT", "ETHDOWNUSDT", "ETHUPUSDT",
    "BNBDOWNUSDT", "BNBUPUSDT", "ADADOWNUSDT", "ADAUPUSDT",
    "XRPDOWNUSDT", "XRPUPUSDT", "DOTDOWNUSDT", "DOTUPUSDT",
    # Low liquidity / high spread
    "PAXGUSDT",
}


class UniverseSelector:
    """Selects and rotates the trading universe dynamically."""

    def __init__(self, api=None, cache_dir="data", top_n=30, min_volume_usd=5_000_000):
        self.api = api
        self.cache_dir = cache_dir
        self.top_n = top_n
        self.min_volume_usd = min_volume_usd
        self._blacklist = self._load_blacklist()
        self._cache = self._load_cache()

    def _load_blacklist(self) -> Set[str]:
        if os.path.exists(BLACKLIST_PATH):
            try:
                with open(BLACKLIST_PATH) as f:
                    data = json.load(f)
                    return set(data.get("symbols", DEFAULT_BLACKLIST))
            except Exception:
                pass
        return set(DEFAULT_BLACKLIST)

    def _save_blacklist(self):
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(BLACKLIST_PATH, "w") as f:
            json.dump({"symbols": list(self._blacklist), "updated": datetime.now().isoformat()}, f, indent=2)

    def _load_cache(self) -> dict:
        if os.path.exists(UNIVERSE_CACHE_PATH):
            try:
                with open(UNIVERSE_CACHE_PATH) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"symbols": [], "updated": "", "expires": ""}

    def _save_cache(self, symbols: List[str]):
        os.makedirs(self.cache_dir, exist_ok=True)
        now = datetime.now()
        with open(UNIVERSE_CACHE_PATH, "w") as f:
            json.dump({
                "symbols": symbols,
                "updated": now.isoformat(),
                "expires": (now + timedelta(hours=1)).isoformat(),
            }, f, indent=2)

    def is_blacklisted(self, symbol: str) -> bool:
        return symbol.upper() in self._blacklist

    def add_to_blacklist(self, symbol: str, reason: str = ""):
        symbol = symbol.upper()
        self._blacklist.add(symbol)
        self._save_blacklist()
        logger.info(f"[Universe] Blacklisted: {symbol} (reason: {reason})")

    def remove_from_blacklist(self, symbol: str):
        symbol = symbol.upper()
        self._blacklist.discard(symbol)
        self._save_blacklist()
        logger.info(f"[Universe] Removed from blacklist: {symbol}")

    def get_blacklist(self) -> List[str]:
        return sorted(self._blacklist)

    def select_universe(self, force_refresh: bool = False) -> List[str]:
        """Return dynamic universe of top-N symbols by volume+volatility score."""
        now = datetime.now()
        expires = self._cache.get("expires", "")
        if not force_refresh and expires and datetime.fromisoformat(expires) > now and self._cache.get("symbols"):
            return self._cache["symbols"]

        if not self.api:
            logger.warning("[Universe] No API available — returning cached symbols")
            return self._cache.get("symbols", [])

        try:
            tickers = self.api.get_24h_ticker()
            scored = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue
                if any(tag in sym for tag in ("UP", "DOWN", "BULL", "BEAR", "_")):
                    continue
                if self.is_blacklisted(sym):
                    continue
                quote_vol = float(t.get("quoteVolume", 0))
                if quote_vol < self.min_volume_usd:
                    continue
                price_change = abs(float(t.get("priceChangePercent", 0)))
                # Score: volume-weighted volatility (prefers liquid + moving coins)
                score = quote_vol * (1 + price_change / 100)
                scored.append((sym, score))

            scored.sort(key=lambda x: x[1], reverse=True)
            symbols = [s for s, _ in scored[:self.top_n]]
            self._save_cache(symbols)
            logger.info(f"[Universe] Selected {len(symbols)} symbols (min vol: ${self.min_volume_usd:,.0f})")
            return symbols
        except Exception as e:
            logger.error(f"[Universe] Selection failed: {e}")
            return self._cache.get("symbols", [])

    def get_universe_stats(self) -> Dict:
        return {
            "total_symbols": len(self._cache.get("symbols", [])),
            "blacklist_size": len(self._blacklist),
            "last_updated": self._cache.get("updated", ""),
            "expires": self._cache.get("expires", ""),
        }
