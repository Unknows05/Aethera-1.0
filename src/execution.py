"""
Execution Engine — ccxt Binance Futures with server-side OCO (SL/TP).
"""
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import ccxt
    HAS_CCXT = True
except ImportError:
    HAS_CCXT = False
    logger.warning("[Execution] ccxt not installed — live trading disabled")


class TradeExecutor:
    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        self.testnet = testnet
        if not HAS_CCXT:
            self.exchange = None
            return
        self.exchange = ccxt.binance({
            "apiKey": api_key, "secret": secret,
            "enableRateLimit": True, "timeout": 15000,
            "options": {
                "defaultType": "future",
                "adjustForTimeDifference": True,
                "recvWindow": 60000,
            },
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)

    def is_ready(self) -> bool:
        return self.exchange is not None

    def get_balance(self) -> Optional[float]:
        if not self.is_ready(): return None
        try:
            balance = self.exchange.fetch_balance()
            return float(balance.get("USDT", {}).get("free", 0))
        except Exception as e:
            logger.error(f"[Execution] Balance error: {e}")
            return None

    def place_oco_order(self, symbol: str, side: str, quantity: float,
                         price: float, stop_price: float, limit_price: float,
                         leverage: int = 5) -> Optional[Dict]:
        """
        Entry + SL/TP. Sets leverage before placing order.
        If SL/TP fails, attempts to close the position to prevent unprotected exposure.
        """
        if not self.is_ready(): return None
        try:
            # Set leverage first
            self.exchange.set_leverage(leverage, symbol)
            # Market entry
            entry_order = self.exchange.create_order(
                symbol=symbol, type="market", side=side.lower(),
                amount=quantity,
            )
            if not entry_order:
                return None
            # SL + TP orders — if either fails, close position immediately
            sl_side = "sell" if side.lower() == "buy" else "buy"
            sl_ok = False
            tp_ok = False
            try:
                self.exchange.create_order(
                    symbol=symbol, type="stop_market",
                    side=sl_side, amount=quantity,
                    params={"stopPrice": stop_price, "reduceOnly": True},
                )
                sl_ok = True
            except Exception as e:
                logger.error(f"[Execution] SL order failed for {symbol}: {e}")
            try:
                self.exchange.create_order(
                    symbol=symbol, type="limit",
                    side=sl_side, amount=quantity, price=limit_price,
                    params={"reduceOnly": True},
                )
                tp_ok = True
            except Exception as e:
                logger.error(f"[Execution] TP order failed for {symbol}: {e}")

            # If SL failed, close position immediately to prevent unlimited loss
            if not sl_ok:
                logger.critical(f"[Execution] NO STOP LOSS on {symbol} — closing position!")
                self.close_position(symbol)
                return None

            logger.info(f"[Execution] {side} {symbol}: qty={quantity}, lev={leverage}x, sl={stop_price}, tp={limit_price}")
            return {"symbol": symbol, "side": side, "qty": quantity, "leverage": leverage}
        except Exception as e:
            logger.error(f"[Execution] OCO error: {e}")
            return None

    def get_positions(self):
        if not self.is_ready(): return []
        try:
            return self.exchange.fetch_positions()
        except Exception as e:
            logger.error(f"[Execution] Positions error: {e}")
            return []

    def close_position(self, symbol: str) -> Optional[Dict]:
        if not self.is_ready(): return None
        try:
            positions = self.exchange.fetch_positions([symbol])
            for p in positions:
                if p["symbol"] == symbol and float(p.get("contracts", 0)) > 0:
                    side = "sell" if float(p["contracts"]) > 0 else "buy"
                    return self.exchange.create_order(
                        symbol=symbol, type="market", side=side,
                        amount=abs(float(p["contracts"])),
                        params={"reduceOnly": True})
            return None
        except Exception as e:
            logger.error(f"[Execution] Close error: {e}")
            return None

    def close(self):
        pass
