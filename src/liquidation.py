"""
Liquidation Heatmap Module
Fetches real liquidation data from Binance using Open Interest + Price action
"""
import asyncio
import aiohttp
import websockets
import json
from typing import Dict, List, Optional, Deque
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time

logger = logging.getLogger(__name__)

@dataclass
class LiquidationData:
    symbol: str
    price: float
    side: str  # "SELL" for long liquidation, "BUY" for short liquidation
    qty: float
    value_usd: float
    timestamp: datetime

class LiquidationHeatmap:
    def __init__(self, max_history: int = 5000):
        self.base_url = "https://fapi.binance.com"
        self.ws_url = "wss://fstream.binance.com/ws/!forceOrder@arr"
        # Use a deque to keep a rolling window of recent liquidations
        self.liquidations: Deque[LiquidationData] = deque(maxlen=max_history)
        self.heatmap_data: Dict = {}
        self.last_update: Optional[datetime] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self.running = False

        
    async def start(self):
        """Start the real-time liquidation stream."""
        self.running = True
        asyncio.create_task(self._listen_forever())
        
    async def _listen_forever(self):
        """Websocket listener for !forceOrder@arr with exponential backoff."""
        backoff = 1  # start at 1 second
        max_backoff = 300  # max 5 minutes
        while self.running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
                ) as ws:
                    logger.info(f"Connected to Liquidation Stream: {self.ws_url}")
                    backoff = 1  # reset on successful connection
                    while self.running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                            await self._process_message(msg)
                        except asyncio.TimeoutError:
                            # No message in 60s — send ping to keep alive
                            await ws.ping()
                            continue
            except Exception as e:
                logger.error(f"Liquidation Stream Error: {e} (retry in {backoff}s)")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                
    async def _process_message(self, message: str):
        """Process real-time liquidation event."""
        try:
            data = json.loads(message)
            # !forceOrder@arr returns a dict with 'o' containing the order details
            order = data.get('o', {})
            if not order:
                return
                
            symbol = order.get('s', '')
            side = order.get('S', '') # SELL = Long Liq, BUY = Short Liq
            qty = float(order.get('q', 0))
            price = float(order.get('p', 0))
            value_usd = qty * price
            
            liq = LiquidationData(
                symbol=symbol,
                price=price,
                side=side,
                qty=qty,
                value_usd=value_usd,
                timestamp=datetime.now()
            )
            
            self.liquidations.append(liq)
            self.last_update = datetime.now()
            
        except Exception as e:
            logger.error(f"Error processing liquidation message: {e}")

    
    def calculate_heatmap(self, min_value: float = 1000) -> Dict:
        """Generate liquidation heatmap data grouped by price zones."""
        if not self.liquidations:
            return {}
        
        # Filter for recent liquidations (e.g., last 1 hour)
        cutoff = datetime.now() - timedelta(hours=1)
        recent = [l for l in self.liquidations if l.timestamp > cutoff and l.value_usd >= min_value]
        
        symbol_data = {}
        for liq in recent:
            symbol = liq.symbol
            if symbol not in symbol_data:
                symbol_data[symbol] = {
                    "total_value": 0,
                    "long_liq": 0,
                    "short_liq": 0,
                    "count": 0,
                    "clusters": {} # Price -> Value
                }
            
            data = symbol_data[symbol]
            data["total_value"] += liq.value_usd
            data["count"] += 1
            
            if liq.side == "SELL":
                data["long_liq"] += liq.value_usd
            else:
                data["short_liq"] += liq.value_usd
                
            # Clustering (round price to 0.5% bins)
            bin_size = liq.price * 0.005
            price_bin = round(liq.price / bin_size) * bin_size
            data["clusters"][price_bin] = data["clusters"].get(price_bin, 0) + liq.value_usd
        
        heatmap = []
        for symbol, data in symbol_data.items():
            # Find top 3 clusters as "magnets"
            top_clusters = sorted(
                [{"price": p, "value": v} for p, v in data["clusters"].items()],
                key=lambda x: x["value"],
                reverse=True
            )[:3]
            
            heatmap.append({
                "symbol": symbol,
                "total_value": round(data["total_value"], 2),
                "long_liquidations": round(data["long_liq"], 2),
                "short_liquidations": round(data["short_liq"], 2),
                "count": data["count"],
                "magnets": top_clusters,
                "intensity": "high" if data["total_value"] > 1000000 else "medium" if data["total_value"] > 100000 else "low"
            })
            
        heatmap.sort(key=lambda x: x["total_value"], reverse=True)
        
        self.heatmap_data = {
            "updated_at": self.last_update.isoformat() if self.last_update else None,
            "total_liquidations": len(recent),
            "total_value": sum(d["total_value"] for d in heatmap),
            "heatmap": heatmap[:30]
        }
        return self.heatmap_data

    async def get_liquidation_levels(self, symbol: str) -> Dict:
        """Get real liquidation clusters for a symbol."""
        if not self.heatmap_data:
            self.calculate_heatmap()
            
        for item in self.heatmap_data.get("heatmap", []):
            if item["symbol"] == symbol:
                return item
        return {}

    def get_summary(self) -> Dict:
        """Get quick summary for dashboard."""
        if not self.heatmap_data:
            return {"status": "no_data"}
            
        heatmap = self.heatmap_data.get("heatmap", [])
        if not heatmap:
            return {"status": "empty"}
            
        top = heatmap[0]
        return {
            "status": "active",
            "top_liquidated": top["symbol"],
            "top_value": top["total_value"],
            "total_value_1h": self.heatmap_data["total_value"],
            "updated_at": self.heatmap_data["updated_at"]
        }

    async def stop(self):
        self.running = False
        logger.info("Liquidation heatmap stopped")

# Global instance
liquidation_heatmap = LiquidationHeatmap()

async def update_liquidation_data():
    """Helper for API compatibility - ensures stream is active."""
    if not liquidation_heatmap.running:
        await liquidation_heatmap.start()
    return True
