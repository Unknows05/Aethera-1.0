"""
Binance Websocket Client - !ticker@arr stream for 500+ symbols.
(Updated v2.1 - HFT Aggregated)
"""
import asyncio
import websockets
import json
import logging
from typing import Dict, Callable, List, Optional
from datetime import datetime
import time
import threading

logger = logging.getLogger(__name__)

class BinanceWebsocketClient:
    def __init__(self, 
                 symbols: List[str],
                 url: str = "wss://fstream.binance.com/ws",
                 buffer_size: int = 10000):
        self.symbols = set(s.upper() for s in symbols)
        self.url = url
        self.buffer_size = buffer_size
        
        # RAM buffer - Protected by asyncio.Lock
        self.price_buffer: Dict[str, Dict] = {}
        self.buffer_lock = asyncio.Lock()
        
        # Handler system
        self.message_handlers: List[Callable] = []
        
        # Connection
        self.websocket = None
        self.reconnect_delay = 1
        self.max_reconnect_delay = 60
        self.running = False
        self.last_message_time = time.time()
        
        # Aggregated stream for all tickers
        self.stream_name = "!ticker@arr"
        
    async def connect(self):
        """Connect with auto-reconnect."""
        max_retries = 0
        while self.running:
            try:
                # Use individual or aggregated stream based on configuration
                # For 500+ symbols, !ticker@arr is mandatory
                stream_url = f"{self.url}/{self.stream_name}"
                
                self.websocket = await asyncio.wait_for(
                    websockets.connect(stream_url, ping_interval=20), 
                    timeout=30.0
                )
                self.reconnect_delay = 1
                max_retries = 0
                logger.info(f"WS Connected to {stream_url}")
                
                await self._listen()
                
            except (websockets.exceptions.ConnectionClosed, 
                    websockets.exceptions.InvalidMessage,
                    asyncio.TimeoutError) as e:
                max_retries += 1
                wait_time = min(self.reconnect_delay * (2 ** min(max_retries, 5)), 
                                self.max_reconnect_delay)
                logger.warning(f"Connection lost. Reconnecting in {wait_time}s")
                await asyncio.sleep(wait_time)
                self.reconnect_delay = wait_time
                
            except Exception as e:
                logger.error(f"Connection error: {e}")
                await asyncio.sleep(self.reconnect_delay)
    
    async def _listen(self):
        """Listening loop."""
        consecutive_empty = 0
        while self.running:
            try:
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(), 
                        timeout=30.0
                    )
                    consecutive_empty = 0
                    await self._process_message(message)
                    
                except asyncio.TimeoutError:
                    if self.websocket and self.websocket.open:
                        await self.websocket.ping()
                    consecutive_empty += 1
                    
                    if consecutive_empty > 60:
                        logger.warning("Stream idle")
                        break
                    continue
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WS closed")
                break
            except Exception as e:
                logger.error(f"Listen error: {e}")
                await asyncio.sleep(1)
                break
    
    async def _process_message(self, message: str):
        """Process ticker array."""
        try:
            data = json.loads(message)
            if isinstance(data, list):
                # Efficient bulk update
                async with self.buffer_lock:
                    now = time.time()
                    for ticker in data:
                        symbol = ticker.get('s', '')
                        if symbol in self.symbols or not self.symbols:
                            self.price_buffer[symbol] = {
                                'price': float(ticker.get('c', 0)),
                                'volume': float(ticker.get('q', 0)), # Use quote volume for better filtering
                                'timestamp': now,
                                'bid': float(ticker.get('b', 0)),
                                'ask': float(ticker.get('a', 0)),
                                'change_pct': float(ticker.get('P', 0)),
                            }
                    self.last_message_time = now
                
                # Call handlers outside lock to prevent deadlocks
                # Note: Handlers will see the updated buffer
                for handler in self.message_handlers:
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            await handler(data)
                        else:
                            handler(data)
                    except Exception as e:
                        logger.error(f"Handler error: {e}")
            
        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.error(f"Process error: {e}")
    
    async def get_price(self, symbol: str) -> Optional[float]:
        """Get price from RAM buffer."""
        symbol = symbol.upper()
        async with self.buffer_lock:
            data = self.price_buffer.get(symbol)
            return data.get('price') if data else None
    
    async def get_all_prices(self) -> Dict[str, float]:
        """Get all prices from buffer."""
        async with self.buffer_lock:
            return {k: v['price'] for k, v in self.price_buffer.items()}
    
    async def start(self):
        """Start client."""
        self.running = True
        self._monitor_task = asyncio.create_task(self._auto_reconnect_monitor())
        await self.connect()
    
    async def _auto_reconnect_monitor(self):
        """Monitor stream health."""
        while self.running:
            await asyncio.sleep(30)
            if time.time() - self.last_message_time > 120:
                logger.warning("No messages for 120s, forcing reconnect")
                if self.websocket and self.websocket.open:
                    await self.websocket.close()
    
    def register_handler(self, handler: Callable):
        """Register handler."""
        if handler not in self.message_handlers:
            self.message_handlers.append(handler)
    
    async def stop(self):
        """Stop."""
        self.running = False
        if hasattr(self, '_monitor_task'):
            self._monitor_task.cancel()
        if self.websocket:
            await self.websocket.close()
        logger.info("WS client stopped")

