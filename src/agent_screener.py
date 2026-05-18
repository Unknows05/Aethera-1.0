"""
Agent Screener — autonomous screening cycle: score → decide → execute.
Handoff to LLM for ambiguous signals (confidence 45-55).
"""
import logging
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


class AgentScreener:
    """Autonomous screening agent: evaluates signals and decides trades."""

    def __init__(self, engine):
        self.engine = engine
        self._busy = False
        self._last_scan_result: Dict = {}

    def run_cycle(self, signals: List[Dict], prices: Dict[str, float]) -> List[Dict]:
        if self._busy:
            logger.debug("[Screener] Busy — skipping cycle")
            return []
        self._busy = True
        executed = []

        try:
            from src.auto_trader import _shared_trader

            # Fetch current open positions + capital
            open_symbols = set()
            open_count = 0
            capital_used = 0.0
            capital = 100.0
            try:
                if _shared_trader and _shared_trader.executor and _shared_trader.executor.is_ready():
                    positions = _shared_trader.executor.get_positions()
                    active = [p for p in positions if float(p.get('contracts', 0)) > 0]
                    open_symbols = {p['symbol'].replace(':USDT', '') for p in active}
                    open_count = len(active)
                    capital = _shared_trader._equity.get("balance", 100)
                    capital_used = _shared_trader._equity.get("used", 0)
            except Exception:
                pass

            # Position limits
            MAX_POSITIONS = 3 if capital < 50 else 5 if capital < 200 else 10
            MAX_PER_SYMBOL = 1
            MAX_CAPITAL_PCT = 0.50

            for signal in signals:
                if signal.get("signal") not in ("LONG", "SHORT"):
                    continue
                if signal.get("risk_blocked"):
                    continue

                symbol = signal.get("symbol", "")
                conf = signal.get("confidence", 50)

                # Skip if already have position on this coin
                if symbol in open_symbols:
                    continue

                # Skip if max positions reached
                if open_count >= MAX_POSITIONS:
                    logger.info(f"[Screener] Max positions ({MAX_POSITIONS}) reached — stopping")
                    break

                # Skip if capital exposure too high
                if capital > 0 and (capital_used / capital) >= MAX_CAPITAL_PCT:
                    logger.info(f"[Screener] Max capital exposure ({MAX_CAPITAL_PCT*100:.0f}%) reached")
                    break

                # HIGH confidence: auto-execute
                if conf >= 65:
                    result = self._execute(signal, "HIGH_CONF")
                    if result.get("executed"):
                        executed.append(result)
                        open_symbols.add(symbol)
                        open_count += 1
                    continue

                # LOW confidence: skip
                if conf < 40:
                    continue

                # AMBIGUOUS (40-64): LLM decide
                if 40 <= conf <= 64:
                    decision = self._llm_decide(signal)
                    if decision.get("decision") in ("LONG", "SHORT"):
                        signal["confidence"] += decision.get("confidence_adjustment", 0)
                        signal["llm_rationale"] = decision.get("rationale", "")
                        result = self._execute(signal, "LLM_DECIDED")
                        if result.get("executed"):
                            executed.append(result)
                            open_symbols.add(symbol)
                            open_count += 1
        finally:
            self._busy = False

        return executed

    def _execute(self, signal: Dict, source: str) -> Dict:
        """Execute a trade signal via auto-trader."""
        from src.auto_trader import _auto_traders, _shared_trader
        executed = False

        # Try SharedAutoTrader (main auto-trader)
        if _shared_trader and _shared_trader.running:
            entry = float(signal.get("entry", signal.get("price", 0)))
            sl = float(signal.get("sl", 0))
            tp = float(signal.get("tp", 0))
            pair = signal.get("symbol", "")
            direction = signal.get("signal", "")
            if entry > 0 and sl > 0:
                from src.position_sizing import calculate_position_size
                capital = _shared_trader._equity.get("balance", 100)
                risk_pct = _shared_trader._settings.get("risk_per_trade_pct", 5) / 100.0
                max_lev = _shared_trader._settings.get("max_leverage", 5)
                sizing = calculate_position_size(
                    capital=capital, risk_pct=risk_pct,
                    entry_price=entry, stop_loss_price=sl,
                    leverage=max_lev, use_maker=True)
                qty = sizing.get("contracts", 0)
                notional = sizing.get("notional", 0)
                if qty > 0 and notional >= 10:
                    side = "buy" if direction == "LONG" else "sell"
                    order = _shared_trader.executor.place_oco_order(
                        symbol=pair, side=side, quantity=qty,
                        price=entry, stop_price=sl, limit_price=tp,
                        leverage=max_lev)
                    if order:
                        executed = True
                        _shared_trader._trade_history.append({
                            "symbol": pair, "side": direction,
                            "entry": entry, "qty": qty, "sl": sl, "tp": tp,
                            "notional": notional, "fee": sizing.get("fee_cost_usd", 0),
                            "time": __import__('datetime').datetime.now().isoformat(),
                        })
                        logger.info(f"[Screener] {source}: {direction} {pair} qty={qty:.4f} @ ${entry:.4f}")

        # Try per-user traders
        for trader in _auto_traders.values():
            if trader.running:
                result = trader.execute_signal(signal)
                if result.get("ok"):
                    executed = True

        from src.decision_log import log_decision
        log_decision(
            symbol=signal.get("symbol", ""),
            signal=signal.get("signal", "WAIT"),
            confidence=signal.get("confidence", 50),
            regime=signal.get("regime", "SIDEWAYS"),
            composite_score=signal.get("composite_score", 50),
            reasons=signal.get("reasons", []),
            ml_confidence=signal.get("ml_confidence", 0),
        )

        return {"symbol": signal.get("symbol"), "executed": executed, "source": source}

    def _llm_decide(self, signal: Dict) -> Dict:
        """Use LLM brain to decide on ambiguous signals."""
        try:
            from src.llm_brain import get_llm_brain
            from src.lessons import get_lessons_summary
            from src.coin_memory import get_coin_note_for_prompt

            llm = get_llm_brain()
            if not llm.is_ready():
                logger.info(f"[Screener] LLM not ready — skipping {signal.get('symbol','?')}")
                return {"decision": "WAIT", "rationale": "LLM not available (no API key)", "confidence_adjustment": 0}

            symbol = signal.get("symbol", "")
            score = signal.get("composite_score", 50)
            conf = signal.get("confidence", 50)
            regime = signal.get("regime", "SIDEWAYS")
            reasons = signal.get("reasons", [])
            logger.info(f"[Screener] LLM deciding for {symbol} conf={conf} score={score:.1f} regime={regime}")

            goal = (f"Coin: {symbol} | Score: {score:.1f} | Signal: {signal.get('signal', '?')} "
                    f"| Confidence: {conf} | Regime: {regime}\n"
                    f"Reasons: {' | '.join(reasons[:5])}\n"
                    f"Decision: LONG / SHORT / WAIT? Reply in JSON.")

            context = {
                "btc_dom": f"{(signal.get('btc_dom_change', 0) or 0)*100:.2f}%",
                "btc_trend": signal.get("btc_trend", 0),
                "market_bias": "RISK-OFF" if signal.get("btc_dom_change", 0) > 0.003 else "NEUTRAL",
                "lessons": get_lessons_summary(),
                "coin_memory": get_coin_note_for_prompt(symbol),
            }

            result = llm.decide_sync(goal, "SCREENER", context)
            logger.info(f"[Screener] LLM result for {symbol}: {result}")
            return result
        except Exception as e:
            logger.error(f"[Screener] LLM error for {signal.get('symbol','?')}: {e}")
            return {"decision": "WAIT", "rationale": f"Error: {e}", "confidence_adjustment": 0}
