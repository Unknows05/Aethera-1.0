"""
Strategy Agent — LLM Strategist ReAct loop for session-level configuration.

Determines: pairs to trade, direction bias, leverage map, confidence thresholds,
max trades, risk per trade, and drawdown limits for the current session.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

from src.bot_context import build_capability_context
from src.config_loader import get_config

logger = logging.getLogger(__name__)

STRATEGIST_SYSTEM_PROMPT = """You are the AETHERA V6 STRATEGIST — a quantitative trading agent.

Your role: at the start of each session, analyze market conditions and configure
the trading system parameters for optimal capital growth.

AVAILABLE TOOLS — call them to gather data before deciding:

1. get_market_overview() → BTC dominance, Fear & Greed, market cap, 24h change
2. scan_tradeable_pairs() → all pairs that meet capital requirements with leverage info
3. get_pair_detail(symbol) → depth on a specific pair: volatility, volume, spread, funding
4. get_portfolio_status() → current positions, PnL, drawdown, daily progress
5. get_lessons(regime, signal) → historical lessons for regime+signal combo (WR, avg PnL)
6. get_session_profile() → current session (Asian/London/NY), typical bias and volatility
7. get_smart_money_flow(symbol) → funding rate, OI, whale positioning, divergence signals
8. update_strategy_config(...) → FINALIZE strategy (call ONCE at the end)

RULES:
- Gather data with tools first (2-4 tool calls), then decide
- update_strategy_config is single-use per session — only call when you have all data
- Balance pairs: 3-5 concurrent positions max
- Diversify across market cap: BTC/ETH + mid-caps + small-caps
- Risk per trade: 2-5% of capital (small-cap) to 1-3% (BTC/ETH)
- Confidence threshold: 60-75% depending on market conditions
- Stop drawdown: 10-20% of capital depending on aggressiveness
- Direction: LONG bias when BTC.D < 50%, SHORT when BTC.D > 55%
- Leverage: 3x-15x depending on volatility and pair

OUTPUT: Call update_strategy_config with JSON containing:
{
  "pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "direction": "LONG",
  "leverage_map": {"BTCUSDT": 10, "ETHUSDT": 10, "SOLUSDT": 7},
  "confidence_threshold": 65,
  "max_trades": 4,
  "risk_per_trade": 0.03,
  "stop_drawdown": 0.15,
  "rationale": "Brief explanation of the strategy configuration",
  "adjusted_target": 0.08
}

TARGET ADAPTATION: If market conditions are unfavorable (sideways, low volatility,
low volume), you MAY adjust the target DOWNWARD to a realistic level. Maximum
adjustment: cannot go below 3% (adjust-min). State your reasoning in the
'rationale' field. If market is good, keep the original target. Use the
'adjusted_target' field in the update_strategy_config JSON (value as fraction,
e.g. 0.08 = 8%)."""


class StrategyAgent:
    def __init__(self):
        self._session_strategy: Optional[Dict] = None
        self._strategy_locked = False

        config = get_config()
        llm_cfg = config.get("llm", {}) if isinstance(config, dict) else {}
        self.model = llm_cfg.get("model", "google/gemini-2.0-flash-001")
        self.base_url = llm_cfg.get("base_url", "https://openrouter.ai/api/v1")
        self.api_key = llm_cfg.get("api_key", "")
        if not self.api_key:
            import os
            self.api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY") or ""

        self.client: Optional[OpenAI] = None
        self._ready = False
        self._connect()

    def _connect(self):
        if not self.api_key:
            logger.warning("[StrategyAgent] No API key — LLM strategist disabled")
            return
        try:
            self.client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=60)
            self._ready = True
            logger.info(f"[StrategyAgent] Ready: {self.model}")
        except Exception as e:
            logger.error(f"[StrategyAgent] Connection failed: {e}")
            self._ready = False

    def is_ready(self) -> bool:
        return self._ready and self.client is not None

    def run_cycle(self, capital: float, target_pct: float, mode: str = "balanced",
                  vault_context: Optional[str] = None) -> Dict:
        if not self.is_ready():
            logger.warning("[StrategyAgent] LLM not ready, returning default strategy")
            return self._default_strategy()

        self._strategy_locked = False
        self._session_strategy = None

        capability_ctx = build_capability_context(capital)
        target_desc = f"${capital * (1 + target_pct):,.2f} (+{target_pct * 100:.1f}%)"

        # Build vault context section
        vault_section = ""
        if vault_context:
            vault_section = f"\n\n─── VAULT KNOWLEDGE ───\n{vault_context}"
        else:
            # Try to load vault context automatically
            try:
                from src.vault.skill_manager import SkillManager
                from src.vault.lesson_manager import LessonManager
                sm = SkillManager()
                lm = LessonManager()
                skills_prompt = sm.get_skills_for_prompt(limit=4)
                lessons_summary = lm.get_lessons_summary(limit=10)
                if skills_prompt or lessons_summary:
                    vault_section = "\n\n─── VAULT KNOWLEDGE ───\n"
                    if skills_prompt:
                        vault_section += skills_prompt + "\n"
                    if lessons_summary:
                        vault_section += lessons_summary
            except Exception:
                pass

        user_prompt = (
            f"Configure the trading strategy for this session.\n\n"
            f"Capital: ${capital:,.2f} | Target: {target_desc} | Mode: {mode}\n"
            f"Use the available tools to analyze market conditions, then call "
            f"update_strategy_config to finalize your configuration.\n\n"
            f"─── SYSTEM CAPABILITY ───\n{capability_ctx}"
            f"{vault_section}"
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": STRATEGIST_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        tools = _build_tool_definitions()

        for step in range(10):
            logger.info(f"[StrategyAgent] Step {step + 1}/10")
            time.sleep(1.0)  # rate limit guard

            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.3,
                    max_tokens=1024,
                    timeout=30,
                )
            except Exception as e:
                logger.error(f"[StrategyAgent] LLM request failed: {e}")
                break

            if not response or not response.choices:
                break

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                result = _parse_json(msg.content)
                if result:
                    return result
                if self._session_strategy:
                    return self._session_strategy
                return self._default_strategy()

            for tc in msg.tool_calls[:3]:
                fn_name = tc.function.name
                fn_args = _parse_json(tc.function.arguments)

                if fn_name == "update_strategy_config" and self._strategy_locked:
                    tool_result = {"blocked": True, "reason": "update_strategy_config already called this session"}
                else:
                    tool_result = self._execute_tool(fn_name, fn_args, capital, target_pct, mode)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result),
                })

        if self._session_strategy:
            return self._session_strategy
        return self._default_strategy()

    def _execute_tool(self, fn_name: str, args: Dict, capital: float,
                      target_pct: float, mode: str) -> Dict:
        if fn_name == "get_market_overview":
            return _tool_market_overview()

        if fn_name == "scan_tradeable_pairs":
            from src.binance_api import BinanceFuturesAPI
            api = BinanceFuturesAPI()
            pairs = api.get_tradeable_pairs_info(capital)
            tradeable = {s: v for s, v in pairs.items() if v.get("can_trade")}
            top20 = dict(sorted(tradeable.items())[:20])
            return {
                "total_pairs": len(pairs),
                "tradeable_count": len(tradeable),
                "top_20_tradeable": {
                    s: {"max_leverage": v["max_leverage"], "min_notional": v["min_notional"]}
                    for s, v in top20.items()
                },
            }

        if fn_name == "get_pair_detail":
            symbol = args.get("symbol", "")
            return _tool_pair_detail(symbol)

        if fn_name == "get_portfolio_status":
            return _tool_portfolio_status(capital, target_pct, mode)

        if fn_name == "get_lessons":
            regime = args.get("regime", "")
            signal = args.get("signal", "")
            return _tool_lessons(regime, signal)

        if fn_name == "get_session_profile":
            return _tool_session_profile()

        if fn_name == "get_smart_money_flow":
            symbol = args.get("symbol", "")
            return _tool_smart_money_flow(symbol)

        if fn_name == "update_strategy_config":
            self._strategy_locked = True
            adjusted = args.get("adjusted_target")
            if adjusted is not None and target_pct > 0:
                diff = abs(adjusted - target_pct) / target_pct
                if diff > 0.20:
                    logger.warning(
                        f"[StrategyAgent] LLM adjusted target {target_pct*100:.1f}% → "
                        f"{adjusted*100:.1f}% (Δ{diff*100:.0f}%) — {args.get('rationale','no rationale')[:80]}"
                    )
                elif diff > 0.01:
                    logger.info(
                        f"[StrategyAgent] LLM adjusted target {target_pct*100:.1f}% → "
                        f"{adjusted*100:.1f}% (Δ{diff*100:.0f}%)"
                    )
            self._session_strategy = {
                "pairs": args.get("pairs", []),
                "direction": args.get("direction", "LONG"),
                "leverage_map": args.get("leverage_map", {}),
                "confidence_threshold": args.get("confidence_threshold", 65),
                "max_trades": args.get("max_trades", 3),
                "risk_per_trade": args.get("risk_per_trade", 0.03),
                "stop_drawdown": args.get("stop_drawdown", 0.15),
                "rationale": args.get("rationale", ""),
                "adjusted_target": adjusted,
            }
            logger.info(
                f"[StrategyAgent] Strategy configured: {self._session_strategy['direction']}, "
                f"{len(self._session_strategy['pairs'])} pairs, "
                f"conf={self._session_strategy['confidence_threshold']}%, "
                f"max_trades={self._session_strategy['max_trades']}"
            )
            # ── Audit Chain: log strategy change ──
            try:
                from src.audit_chain import get_audit_chain
                chain = get_audit_chain()
                chain.append({
                    "type": "strategy_change",
                    "direction": self._session_strategy["direction"],
                    "pairs": self._session_strategy["pairs"],
                    "confidence_threshold": self._session_strategy["confidence_threshold"],
                    "max_trades": self._session_strategy["max_trades"],
                    "risk_per_trade": self._session_strategy["risk_per_trade"],
                    "stop_drawdown": self._session_strategy["stop_drawdown"],
                    "rationale": self._session_strategy["rationale"],
                })
            except Exception:
                pass
            return {"success": True, "strategy": self._session_strategy}

        return {"error": f"Unknown tool: {fn_name}"}

    def _default_strategy(self) -> Dict:
        return {
            "pairs": ["BTCUSDT"],
            "direction": "LONG",
            "leverage_map": {"BTCUSDT": 5},
            "confidence_threshold": 65,
            "max_trades": 2,
            "risk_per_trade": 0.02,
            "stop_drawdown": 0.15,
            "rationale": "Default conservative strategy (LLM unavailable)",
        }


def _build_tool_definitions() -> List[Dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_market_overview",
                "description": "Get macro market overview: BTC dominance, Fear & Greed, 24h change.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scan_tradeable_pairs",
                "description": "Scan all PERPETUAL USDT-M pairs that meet capital requirements.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_pair_detail",
                "description": "Get detailed metrics for a specific trading pair: volatility, volume, spread, funding rate.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Trading pair symbol e.g. BTCUSDT"}
                    },
                    "required": ["symbol"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_portfolio_status",
                "description": "Get current portfolio: open positions, PnL, daily progress, drawdown.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_lessons",
                "description": "Get historical lessons learned for a regime+signal combo.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "regime": {"type": "string", "description": "Market regime: BULL, BEAR, SIDEWAYS"},
                        "signal": {"type": "string", "description": "Trade signal: LONG, SHORT"}
                    },
                    "required": ["regime", "signal"],
                },
            },
        },
            {
            "type": "function",
            "function": {
                "name": "get_session_profile",
                "description": "Get current trading session profile (Asian/London/NY) with typical bias.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_smart_money_flow",
                "description": "Get smart money positioning: funding rate, open interest change, whale vs retail positioning, divergence signals",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Trading pair symbol e.g. DOGEUSDT"}
                    },
                    "required": ["symbol"]
                }
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_strategy_config",
                "description": "Finalize the strategy configuration. CALL THIS ONCE when ready.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pairs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of symbols to trade e.g. ['BTCUSDT', 'ETHUSDT']",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["LONG", "SHORT", "NEUTRAL"],
                            "description": "Primary direction bias",
                        },
                        "leverage_map": {
                            "type": "object",
                            "description": "Symbol to leverage mapping e.g. {'BTCUSDT': 10, 'ETHUSDT': 7}",
                        },
                        "confidence_threshold": {
                            "type": "integer",
                            "minimum": 40,
                            "maximum": 90,
                            "description": "Minimum confidence % to take a trade",
                        },
                        "max_trades": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "description": "Maximum concurrent open positions",
                        },
                        "risk_per_trade": {
                            "type": "number",
                            "minimum": 0.005,
                            "maximum": 0.10,
                            "description": "Fraction of capital to risk per trade (0.03 = 3%)",
                        },
                        "stop_drawdown": {
                            "type": "number",
                            "minimum": 0.05,
                            "maximum": 0.30,
                            "description": "Session stop drawdown limit (0.15 = 15%)",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "Brief explanation of why this config is chosen",
                        },
                        "adjusted_target": {
                            "type": "number",
                            "minimum": 0.03,
                            "maximum": 0.20,
                            "description": "LLM-adjusted daily target as fraction (e.g. 0.08 = 8%). Only set if market conditions warrant adjusting from original target.",
                        },
                    },
                    "required": [
                        "pairs", "direction", "leverage_map", "confidence_threshold",
                        "max_trades", "risk_per_trade", "stop_drawdown",
                    ],
                },
            },
        },
    ]


def _parse_json(text: Optional[str]) -> Dict:
    if not text:
        return {}
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```json" in text:
        try:
            inner = text.split("```json")[1].split("```")[0].strip()
            return json.loads(inner)
        except (json.JSONDecodeError, IndexError):
            pass
    return {}


def _tool_market_overview() -> Dict:
    from src.binance_api import BinanceFuturesAPI
    api = BinanceFuturesAPI()
    try:
        tickers = api.get_24h_ticker()
        btc = next((t for t in tickers if t.get("symbol") == "BTCUSDT"), {})
        return {
            "btc_price": float(btc.get("lastPrice", 0)),
            "btc_24h_change_pct": float(btc.get("priceChangePercent", 0)),
            "btc_volume_24h": float(btc.get("quoteVolume", 0)),
            "total_symbols_scanned": len([t for t in tickers if float(t.get("quoteVolume", 0)) > 0]),
        }
    except Exception as e:
        logger.error(f"[StrategyAgent] get_market_overview failed: {e}")
        return {"error": str(e)}


def _tool_pair_detail(symbol: str) -> Dict:
    from src.binance_api import BinanceFuturesAPI
    api = BinanceFuturesAPI()
    try:
        ticker = api.get_24h_ticker(symbol.upper())
        klines = api.get_klines(symbol.upper(), interval="1h", limit=24)
        if ticker and isinstance(ticker, list) and ticker:
            t = ticker[0]
        else:
            t = {}
        if klines:
            closes = [k["close"] for k in klines]
            returns = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    returns.append(abs(closes[i] - closes[i - 1]) / closes[i - 1])
            avg_hourly_vol = sum(returns) / len(returns) * 100 if returns else 0
        else:
            avg_hourly_vol = 0
        return {
            "symbol": symbol,
            "price": float(t.get("lastPrice", 0)),
            "change_24h_pct": float(t.get("priceChangePercent", 0)),
            "volume_24h": float(t.get("quoteVolume", 0)),
            "high_24h": float(t.get("highPrice", 0)),
            "low_24h": float(t.get("lowPrice", 0)),
            "avg_hourly_volatility_pct": round(avg_hourly_vol, 2),
        }
    except Exception as e:
        logger.error(f"[StrategyAgent] get_pair_detail({symbol}) failed: {e}")
        return {"error": str(e), "symbol": symbol}


def _tool_portfolio_status(capital: float, target_pct: float, mode: str) -> Dict:
    try:
        import os
        import json as _json
        state_path = "data/daily_state.json"
        daily_state = {}
        if os.path.exists(state_path):
            with open(state_path) as f:
                daily_state = _json.load(f)
        return {
            "capital_start": daily_state.get("capital_start", capital),
            "current_pnl": daily_state.get("current_pnl", 0),
            "trades_taken": daily_state.get("trades_taken", 0),
            "target_pct": daily_state.get("target_pct", target_pct),
            "mode": mode,
            "progress_pct": daily_state.get("current_pnl", 0) / capital * 100 if capital > 0 else 0,
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_lessons(regime: str, signal: str) -> Dict:
    from src.lessons import _load as lessons_load
    try:
        all_lessons = lessons_load()
        filtered = [
            l for l in all_lessons
            if l.get("regime", "").upper() == regime.upper()
            and l.get("signal", "").upper() == signal.upper()
        ] if regime and signal else all_lessons[-20:]
        wins = sum(1 for l in filtered if l.get("result") == "WIN")
        losses = sum(1 for l in filtered if l.get("result") == "LOSS")
        wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
        avg_pnl = sum(l.get("pnl_pct", 0) for l in filtered) / len(filtered) if filtered else 0
        recent_lessons = [l.get("lesson", "") for l in filtered[-5:]]
        return {
            "regime": regime,
            "signal": signal,
            "total": len(filtered),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wr, 1),
            "avg_pnl": round(avg_pnl, 2),
            "recent_lessons": recent_lessons,
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_session_profile() -> Dict:
    from datetime import datetime
    hour = datetime.utcnow().hour
    if 0 <= hour < 9:
        session = "Asian"
        bias = "RANGE"
    elif 9 <= hour < 16:
        session = "London"
        bias = "TREND"
    else:
        session = "NY"
        bias = "MOMENTUM"
    return {"session": session, "bias": bias, "utc_hour": hour}


def _tool_smart_money_flow(symbol: str) -> Dict:
    try:
        from src.binance_api import BinanceFuturesAPI
        from src.enhanced_data import get_enhanced_data

        api = BinanceFuturesAPI()

        funding_data = api.get_funding_rate(symbol, limit=10)
        if funding_data and len(funding_data) > 0:
            current_funding = float(funding_data[-1].get("fundingRate", 0))
            if len(funding_data) >= 2:
                oldest_funding = float(funding_data[0].get("fundingRate", 0))
                funding_velocity = (current_funding - oldest_funding) / len(funding_data) * 10000
            else:
                funding_velocity = 0
        else:
            current_funding = 0
            funding_velocity = 0

        oi_data = api.get_open_interest(symbol)
        oi_value = float(oi_data.get("openInterest", 0))

        klines = api.get_klines(symbol, "1h", 96)
        if klines and len(klines) >= 2:
            first_vol = float(klines[0].get("volume", 0))
            last_vol = float(klines[-1].get("volume", 0))
            oi_change_24h_pct = ((last_vol - first_vol) / first_vol * 100) if first_vol > 0 else 0
        else:
            oi_change_24h_pct = 0

        enhanced = get_enhanced_data()
        metrics = enhanced.get_enhanced_metrics(symbol)

        tt = metrics.get("topTrader", {}) or {}
        ls = metrics.get("longShortRatio", {}) or {}
        top_trader_long = float(tt.get("longRatio", 0.5))
        retail_long_pct = ls.get("latest_long_pct")
        retail_long = float(retail_long_pct) / 100.0 if retail_long_pct is not None else 0.5
        smart_retail_gap = top_trader_long - retail_long

        if oi_change_24h_pct > 10 and abs(current_funding) < 0.0001:
            oi_divergence = "bearish"
        elif oi_change_24h_pct < -10 and current_funding > 0.0005:
            oi_divergence = "bullish"
        else:
            oi_divergence = "neutral"

        if smart_retail_gap < -0.2 and current_funding > 0.0001:
            interpretation = "whales positioning SHORT while retail LONG — potential retail squeeze"
        elif smart_retail_gap > 0.2 and current_funding < -0.0001:
            interpretation = "whales positioning LONG while retail SHORT — funding pays whales"
        elif smart_retail_gap > 0.2:
            interpretation = "whales positioning LONG — smart money accumulating"
        elif smart_retail_gap < -0.2:
            interpretation = "whales positioning SHORT — smart money distributing"
        elif oi_divergence == "bearish":
            interpretation = "OI rising with flat funding — divergence warning"
        elif oi_divergence == "bullish":
            interpretation = "OI dropping with positive funding — capitulation signal"
        else:
            interpretation = "neutral positioning — no clear divergence"

        return {
            "symbol": symbol,
            "funding_rate": round(current_funding, 6),
            "funding_velocity_bps": round(funding_velocity, 2),
            "open_interest_usd": oi_value,
            "oi_change_24h_pct": round(oi_change_24h_pct, 2),
            "whale_long_pct": round(top_trader_long * 100, 1),
            "retail_long_pct": round(retail_long * 100, 1),
            "smart_retail_gap": round(smart_retail_gap, 1),
            "oi_divergence_signal": oi_divergence,
            "interpretation": interpretation,
        }
    except Exception as e:
        logger.error(f"[StrategyAgent] get_smart_money_flow({symbol}) failed: {e}")
        return {"error": str(e), "symbol": symbol}
