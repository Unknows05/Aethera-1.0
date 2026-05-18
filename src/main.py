#!/usr/bin/env python3
"""
Main entry point - Websocket-based monitoring.
"""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.config import load_config
from src.websocket_binance import BinanceWebsocketClient
from src.scorer import Scorer
from src.ml_engine import get_ml_engine
from src.engine_v2 import ScreeningEngineV2 as ScreeningEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def main():
    """Main async entry point."""
    config = load_config()
    
    # Load symbols from config
    symbols = config.get('symbols', [
        'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT',
        'DOGEUSDT', 'SOLUSDT', 'DOTUSDT', 'AVAXUSDT', 'LINKUSDT'
    ])
    
    logger = logging.getLogger(__name__)
    logger.info(f"Starting with {len(symbols)} symbols")
    
    # Initialize components
    scorer = Scorer(config)
    ml_engine = get_ml_engine()
    engine = ScreeningEngine(config, cache_dir="data")
    
    # Create websocket client
    ws_client = BinanceWebsocketClient(
        symbols=symbols,
        buffer_size=config.get('websocket', {}).get('buffer_size', 10000)
    )
    
    # Register engine as handler (Stage 1 Triage)
    ws_client.register_handler(engine.handle_ticker_stream)
    
    # Start liquidation stream
    from src.liquidation import liquidation_heatmap
    await liquidation_heatmap.start()
    
    # Start websocket in background
    ws_task = asyncio.create_task(ws_client.start())

    # Background Maintenance Task
    async def maintenance_loop():
        while True:
            try:
                # 1. Resolve Signal Outcomes
                prices = await ws_client.get_all_prices()
                if prices:
                    await engine.db.check_outcomes(prices)
                
                logger.info("[Maintenance] Outcomes resolved and learning updated")
            except Exception as e:
                logger.error(f"[Maintenance] Error: {e}")
            
            await asyncio.sleep(300)  # Every 5 minutes
    
    maintenance_task = asyncio.create_task(maintenance_loop())
    
    logger.info("System running. Press Ctrl+C to stop.")
    
    try:
        # Keep main task alive
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        ws_task.cancel()
        maintenance_task.cancel()
        await ws_client.stop()
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    asyncio.run(main())
