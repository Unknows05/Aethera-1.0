"""
Engine V2 — async market screening with parallel data fetch + ML filtering.
Screens 30 symbols, deep-scans top 30, saves signals to DB.
"""
import logging
import os
import asyncio
from typing import Dict, List, Optional
from datetime import datetime

from src.decision_log import log_decision
from src.agent_screener import AgentScreener
from src.agent_manager import AgentManager
from src.cooldown import TradeCooldown
from src.trailing_tp import TrailingTP
from src.universe_selector import UniverseSelector
from src.market_regime import get_market_regime
from src.vault.indexer import VaultIndexer
from src.vault.skill_manager import SkillManager
from src.vault.lesson_manager import LessonManager
from src.vault.memory import VaultMemory
from src.vault.search import VaultSearch
from src.vault.backup import VaultBackup

logger = logging.getLogger(__name__)


class ScreeningEngineV2:
    def __init__(self, config=None, cache_dir="data"):
        from src.scorer import Scorer
        from src.binance_api import BinanceFuturesAPI
        from src.enhanced_data import get_enhanced_data
        from src.risk_manager import get_risk_manager, RiskConfig
        from src.database import ScreenerDB
        from src.alerter import SignalAlerter
        from src.signals import generate_signal
        from src.feature_extractor import FeatureExtractor
        from src.ml_engine import get_ml_engine
        from src.telegram_bot import get_telegram_bot
        from src.position_manager import PositionManager

        self.config = config or {}
        self.cache_dir = cache_dir
        self.symbols = self.config.get('symbols', [])

        db_path = os.path.join(cache_dir, "screener.db")

        self.scorer = Scorer()
        self.api = BinanceFuturesAPI()
        self.enhanced_data = get_enhanced_data()
        self.risk_manager = get_risk_manager(RiskConfig(), db_path)
        self.db = ScreenerDB(db_path)
        self.alerter = SignalAlerter()
        self.generate_signal = generate_signal
        self.ml_engine = get_ml_engine(db_path)
        self.feature_extractor = FeatureExtractor(db_path)
        self.telegram = get_telegram_bot(config.get("telegram", {}))
        self.position_manager = PositionManager(self.db, self.risk_manager)

        self.last_scan_time: Dict[str, float] = {}
        self.latest_results: Dict[str, Dict] = {}
        self._last_full_scan: Optional[Dict] = None
        self._next_scan: str = "-"
        self._is_scanning: bool = False
        self._btc_klines_cache: Optional[List[Dict]] = None
        self._btc_dom_change: float = 0.0
        self._scan_count: int = 0
        self._last_scan_time: Optional[str] = None
        self._premium_cache: Dict = {}
        self._strategy: Dict = {}
        self._strategy_direction: str = "BOTH"
        self._strategy_conf: int = 0
        self._strategy_leverage: Dict = {}

        self.cooldown = TradeCooldown()
        self.trailing_tp = TrailingTP()
        self._trade_count_since_nudge: int = 0

        # Dynamic universe selector
        self.universe = UniverseSelector(api=self.api, cache_dir=cache_dir,
                                         top_n=config.get("universe", {}).get("top_n", 30),
                                         min_volume_usd=config.get("universe", {}).get("min_volume_usd", 5_000_000))
        # Refresh universe on first scan if enabled
        self._universe_refreshed = False
        self._use_dynamic_universe = config.get("universe", {}).get("dynamic", False)

        # Market-wide regime detector
        self.market_regime = get_market_regime(api=self.api)
        self._market_regime_data: Dict = {"regime": "SIDEWAYS", "confidence": 0.5}

        # Knowledge Vault (Obsidian-style)
        vault_dir = config.get("vault", {}).get("dir", "vault")
        self.vault_indexer = VaultIndexer(vault_dir=vault_dir)
        self.vault_skills = SkillManager(vault_dir=vault_dir, indexer=self.vault_indexer)
        self.vault_lessons = LessonManager(vault_dir=vault_dir, indexer=self.vault_indexer)
        self.vault_memory = VaultMemory(vault_dir=vault_dir)
        self.vault_search = VaultSearch(indexer=self.vault_indexer)
        self.vault_backup = VaultBackup(vault_dir=vault_dir)

        # Debate Pipeline (Phase 3)
        llm_config = config.get("llm", {})
        llm_api_key = llm_config.get("api_key", "") or os.getenv("OPENROUTER_API_KEY", "")
        llm_model = llm_config.get("model", "deepseek/deepseek-chat-v4:free")
        llm_base_url = llm_config.get("base_url", "https://openrouter.ai/api/v1")

        from src.agents.bull_agent import BullAgent
        from src.agents.bear_agent import BearAgent
        from src.agents.synthesizer import Synthesizer
        from src.agents.debate import DebateOrchestrator
        from src.agents.risk_gate import RiskGate

        self.bull_agent = BullAgent(model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        self.bear_agent = BearAgent(model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        self.synthesizer = Synthesizer(model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        self.debate_orchestrator = DebateOrchestrator(
            bull_agent=self.bull_agent,
            bear_agent=self.bear_agent,
            synthesizer=self.synthesizer,
        )
        self.risk_gate = RiskGate(config.get("risk_gate", {}))
        self._debate_enabled = config.get("debate", {}).get("enabled", True)
        self._debate_min_confidence = config.get("debate", {}).get("min_confidence", 50)

        # ReAct Agent (LLM Orchestrator — Meridian-style)
        self.react_agent = None
        self.react_executor = None
        react_enabled = config.get("react_agent", {}).get("enabled", True)
        if react_enabled and llm_api_key:
            try:
                from openai import OpenAI
                from src.agents.tools.executor import ToolExecutor
                from src.agents.react_loop import ReactAgent
                react_model = config.get("react_agent", {}).get("model", llm_model)
                react_client = OpenAI(base_url=llm_base_url, api_key=llm_api_key, timeout=60)
                self.react_executor = ToolExecutor(
                    engine=self,
                    vault_search=self.vault_search,
                    hivemind=None,  # Set after hivemind client is available
                )
                self.react_agent = ReactAgent(
                    client=react_client,
                    executor=self.react_executor,
                    model=react_model,
                    max_steps=config.get("react_agent", {}).get("max_steps", 15),
                )
                logger.info(f"[EngineV2] ReAct agent enabled (model={react_model})")
            except Exception as e:
                logger.warning(f"[EngineV2] ReAct agent init failed: {e}")

        # Initial vault index
        try:
            self.vault_indexer.index_all()
        except Exception as e:
            logger.debug(f"[EngineV2] Vault index error: {e}")

        self._validate_symbols()
        logger.info(f"[EngineV2] Initialized — {len(self.symbols)} symbols, ML v4")

    def set_strategy(self, strategy: Dict):
        """Apply LLM strategist configuration to filter scans."""
        self._strategy = strategy
        if strategy.get("pairs"):
            self.symbols = strategy["pairs"]
            logger.info(f"[EngineV2] Strategy active — {len(self.symbols)} pairs, "
                       f"direction={strategy.get('direction','BOTH')}, "
                       f"conf≥{strategy.get('confidence_threshold',0)}, "
                       f"max={strategy.get('max_trades',3)} trades")
        if strategy.get("direction"):
            self._strategy_direction = strategy["direction"]
        if strategy.get("confidence_threshold"):
            self._strategy_conf = strategy["confidence_threshold"]
        if strategy.get("leverage_map"):
            self._strategy_leverage = strategy["leverage_map"]

        # Audit chain: log strategy change
        try:
            from src.audit_chain import get_audit_chain
            audit_chain = get_audit_chain()
            audit_chain.append({
                "type": "strategy_change",
                "direction": strategy.get("direction"),
                "pairs": strategy.get("pairs", []),
                "confidence_threshold": strategy.get("confidence_threshold", 0),
                "max_trades": strategy.get("max_trades", 0),
                "rationale": strategy.get("rationale", ""),
            })
        except Exception:
            pass

    def get_strategy(self) -> Dict:
        return self._strategy if self._strategy else {"pairs": self.symbols, "direction": "BOTH", "rationale": "No LLM strategy set"}

    def set_next_scan(self, iso_time: str):
        self._next_scan = iso_time

    def is_scanning(self) -> bool:
        return self._is_scanning

    async def scan(self) -> Dict:
        """Full market scan: quick score all symbols → deep scan top 30."""
        logger.info("[EngineV2] Scan started")
        self._is_scanning = True
        try:
            # Refresh dynamic universe on first scan if enabled
            if self._use_dynamic_universe and not self._universe_refreshed:
                new_symbols = self.universe.select_universe(force_refresh=True)
                if new_symbols:
                    self.symbols = new_symbols
                    self._universe_refreshed = True
                    logger.info(f"[EngineV2] Dynamic universe: {len(self.symbols)} symbols")

            # Filter out blacklisted symbols
            active_symbols = [s for s in self.symbols if not self.universe.is_blacklisted(s)]
            if len(active_symbols) < len(self.symbols):
                logger.info(f"[EngineV2] Filtered {len(self.symbols) - len(active_symbols)} blacklisted symbols")

            # Stage 1: Quick scan all symbols
            quick_tasks = [self._quick_scan(s) for s in active_symbols]
            quick_results = await asyncio.gather(*quick_tasks, return_exceptions=True)

            valid = [r for r in quick_results if isinstance(r, dict) and r.get('score', 0) > 0]
            valid.sort(key=lambda x: x.get('score', 0), reverse=True)
            top_30 = valid[:10]  # Top 10 for fast startup (reduced from 30)

            # Cooldown filter: skip blocked symbols
            _filtered = []
            _blocked = []
            for r in top_30:
                if self.cooldown.should_block(r['symbol']):
                    _blocked.append(r['symbol'])
                else:
                    _filtered.append(r)
            if _blocked:
                try:
                    from src.audit_chain import get_audit_chain
                    audit_chain = get_audit_chain()
                    for sym in _blocked:
                        audit_chain.append({"type": "cooldown_block", "symbol": sym, "reason": "cooldown_active"})
                except Exception:
                    pass
            top_30 = _filtered

            # Stage 1.5: Fetch BTC 4h once for all ML features + market regime
            loop = asyncio.get_event_loop()
            self._btc_klines_cache = await loop.run_in_executor(None, self.api.get_klines, "BTCUSDT", "4h", 100)

            # Refresh market-wide regime
            if self.market_regime:
                self.market_regime.fetch_btc_data()
                ticker_data_for_regime = await loop.run_in_executor(None, self.api.get_24h_ticker)
                self.market_regime.fetch_market_data(ticker_data_for_regime)
                self._market_regime_data = self.market_regime.detect_regime()
                logger.info(f"[EngineV2] Market regime: {self._market_regime_data['regime']} "
                           f"(conf={self._market_regime_data['confidence']})")

            # Stage 2: Deep scan top 30 in parallel
            deep_tasks = [self.run_deep_scan(r['symbol']) for r in top_30 if r.get('symbol')]
            deep_results = await asyncio.gather(*deep_tasks, return_exceptions=True)

            valid_deep = [r for r in deep_results if isinstance(r, dict)]
            ts = datetime.now().isoformat()
            self._last_scan_time = ts
            self._scan_count += 1
            self.alerter.check(valid_deep, ts)
            self._update_full_scan_snapshot()

            # Position Management — check if any open positions need action
            try:
                ticker_data = await loop.run_in_executor(None, self.api.get_24h_ticker)
                prices = {t["symbol"]: float(t["lastPrice"]) for t in ticker_data}
                regime = self._get_current_market_regime()
                actions = self.position_manager.manage_all(prices, regime)
                for action in actions:
                    logger.info(f"[EngineV2] Position action: {action}")
            except Exception as e:
                logger.debug(f"[EngineV2] Position mgmt skipped: {e}")

            # Feed signals to auto-trader (AgentScreener)
            try:
                from src.agent_screener import AgentScreener
                from src.auto_trader import _shared_trader as _st
                if _st and _st.running:
                    screener = AgentScreener(self)
                    ticker_data = await loop.run_in_executor(None, self.api.get_24h_ticker)
                    prices = {t["symbol"]: float(t["lastPrice"]) for t in ticker_data}
                    screener.run_cycle(valid_deep, prices)
            except Exception as e:
                logger.debug(f"[EngineV2] Auto-trader: {e}")

            # Learning loop: simulate trade outcomes + hook skill/memory/curator/goal
            try:
                await self._run_learning_loop(valid_deep)
            except Exception as e:
                logger.debug(f"[EngineV2] Learning loop: {e}")

            result = self.get_latest_scan()
            logger.info(f"[EngineV2] Scan done: {result.get('summary', {})}")
            return result
        except Exception as e:
            logger.error(f"[EngineV2] Scan failed: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            self._is_scanning = False

    def _get_current_market_regime(self) -> str:
        """Return market-wide regime from detector, fallback to coin-aggregate."""
        if self._market_regime_data.get("regime"):
            return self._market_regime_data["regime"]
        if not self.latest_results:
            return "SIDEWAYS"
        regimes = [v.get("regime", "SIDEWAYS") for v in self.latest_results.values()]
        if not regimes: return "SIDEWAYS"
        bull = sum(1 for r in regimes if r == "BULL")
        bear = sum(1 for r in regimes if r == "BEAR")
        high = sum(1 for r in regimes if r == "HIGH_VOL")
        return "BULL" if bull > bear > high else "BEAR" if bear > bull > high else "HIGH_VOL" if high > max(bull, bear) else "SIDEWAYS"

    async def _quick_scan(self, symbol: str) -> Dict:
        try:
            loop = asyncio.get_event_loop()
            klines = await loop.run_in_executor(None, self.api.get_klines, symbol, "15m", 50)
            if not klines or len(klines) < 20:
                return {"symbol": symbol, "score": 0}
            last = klines[-1]
            close = float(last['close'])
            closes = [float(k['close']) for k in klines[-20:]]
            ma20 = sum(closes) / len(closes)
            momentum = ((close - ma20) / ma20 * 100) if ma20 > 0 else 0
            volumes = [float(k['volume']) for k in klines[-10:]]
            avg_vol = sum(volumes) / len(volumes)
            vol_ratio = (float(last['volume']) / avg_vol) if avg_vol > 0 else 1.0
            score = 50 + momentum * 2 + (vol_ratio - 1) * 10
            score = max(0, min(100, score))
            return {"symbol": symbol, "score": round(score, 2), "vol_ratio": round(vol_ratio, 2)}
        except Exception as e:
            logger.debug(f"[EngineV2] Quick scan error {symbol}: {e}")
            return {"symbol": symbol, "score": 0}

    async def run_deep_scan(self, symbol: str) -> Dict:
        """Deep analysis — parallel API calls for klines, enhanced data, OI."""
        try:
            loop = asyncio.get_event_loop()

            klines_15m_coro = loop.run_in_executor(None, self.api.get_klines, symbol, "15m", 100)
            klines_1h_coro = loop.run_in_executor(None, self.api.get_klines, symbol, "1h", 100)
            klines_4h_coro = loop.run_in_executor(None, self.api.get_klines, symbol, "4h", 100)
            enhanced_coro = loop.run_in_executor(None, self.enhanced_data.get_enhanced_metrics, symbol)
            oi_1h_coro = loop.run_in_executor(None, self.enhanced_data.get_oi_change, symbol, 1)
            oi_4h_coro = loop.run_in_executor(None, self.enhanced_data.get_oi_change, symbol, 4)
            oi_24h_coro = loop.run_in_executor(None, self.enhanced_data.get_oi_change, symbol, 24)

            results = await asyncio.gather(
                klines_15m_coro, klines_1h_coro, klines_4h_coro,
                enhanced_coro, oi_1h_coro, oi_4h_coro, oi_24h_coro,
                return_exceptions=True,
            )

            klines = results[0]
            klines_1h = results[1] if not isinstance(results[1], Exception) else []
            klines_4h = results[2] if not isinstance(results[2], Exception) else []
            enhanced = results[3] if not isinstance(results[3], Exception) else {}
            oi_1h = results[4] if not isinstance(results[4], Exception) else 0
            oi_4h = results[5] if not isinstance(results[5], Exception) else 0
            oi_24h = results[6] if not isinstance(results[6], Exception) else 0

            # Build tf_klines dict for multi-timeframe access
            tf_klines = {"15m": klines, "1h": klines_1h, "4h": klines_4h}

            if not klines or isinstance(klines, Exception):
                return {"ok": False}

            # Merge OI into enhanced — NEVER replace, only add missing
            if not isinstance(enhanced, dict):
                enhanced = {}
            enhanced.setdefault("oi_change_1h", oi_1h)
            enhanced.setdefault("oi_change_4h", oi_4h)
            enhanced.setdefault("oi_change_24h", oi_24h)
            enhanced.setdefault("cvd_1h", 0.0)
            enhanced.setdefault("orderbook_imbalance", 0.0)
            enhanced.setdefault("funding_z_val", 0.0)
            enhanced.setdefault("volume_ma_ratio", 1.0)
            enhanced.setdefault("spread_pct", 0.0)
            if "topTrader" not in enhanced:
                enhanced["topTrader"] = {"longRatio": 0.5, "shortRatio": 0.5}

            # BTC trend from cached klines
            btc_trend = 0
            if self._btc_klines_cache and len(self._btc_klines_cache) >= 21:
                btc_closes = [float(k['close']) for k in self._btc_klines_cache]
                btc_ema9 = sum(btc_closes[-9:]) / 9
                btc_ema21 = sum(btc_closes[-21:]) / 21
                if btc_ema9 > btc_ema21 * 1.01: btc_trend = 1
                elif btc_ema9 < btc_ema21 * 0.99: btc_trend = -1

            # Regime detection with BTC anchor
            regime_type = self.scorer.detect_regime(enhanced, btc_trend)

            # TF metrics for breakout detection
            tf_metrics = {}
            for tf in ["1h", "4h"]:
                k = tf_klines.get(tf, [])
                if k and len(k) >= 2:
                    last_k = k[-1]
                    prev_k = k[-2]
                    tf_metrics[tf] = {
                        "breakout_bull": last_k["close"] > prev_k["high"],
                        "breakout_bear": last_k["close"] < prev_k["low"],
                        "rsi": self.scorer._get_rsi(k) if hasattr(self.scorer, '_get_rsi') else 50,
                        "vol_z": enhanced.get(f"vol_z_{tf}", 0),
                    }

            # 15m breakout
            if klines and len(klines) >= 2:
                lk15 = klines[-1]
                pk15 = klines[-2]
                tf_metrics["15m"] = {
                    "breakout_bull": lk15["close"] > pk15["high"],
                    "breakout_bear": lk15["close"] < pk15["low"],
                    "rsi": self.scorer._get_rsi(klines) if hasattr(self.scorer, '_get_rsi') else 50,
                    "vol_z": 0,
                }

            # Scorer data prep
            last_kline = klines[-1]
            if klines and len(klines) >= 20:
                closes = [float(k['close']) for k in klines]
                ma20 = sum(closes[-20:]) / 20
                ma50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 50 else ma20
                std20 = (sum((c - ma20) ** 2 for c in closes[-20:]) / 20) ** 0.5
                bb_mid = ma20
                bb_upper = ma20 + 2 * std20
                bb_lower = ma20 - 2 * std20
            else:
                ma20 = ma50 = bb_mid = bb_upper = bb_lower = last_kline["close"]

            scorer_data = {
                "symbol": symbol, "close": last_kline["close"],
                "ma20": ma20, "ma50": ma50,
                "bb_mid": bb_mid, "bb_upper": bb_upper, "bb_lower": bb_lower,
                "oi_change_pct": enhanced.get("oi_change_24h", 0),
                "funding_velocity": enhanced.get("funding_velocity", 0),
                **enhanced,
            }
            score_result = self.scorer.calculate(scorer_data, regime_type)

            # Feature extraction for ML
            hours_since = self._get_hours_since_last_signal(symbol)
            btc_klines = self._btc_klines_cache
            features = self.feature_extractor.extract_all(
                symbol=symbol, enhanced=enhanced,
                klines_1h=tf_klines.get("1h", []),
                klines_4h=tf_klines.get("4h", []),
                composite_score=score_result.get("composite_score", 50) if isinstance(score_result, dict) else 50,
                regime=regime_type, db_hours_since=hours_since,
                btc_klines_4h=btc_klines,
            )

            # Generate signal
            coin_data = {
                "symbol": symbol, "price": last_kline["close"],
                "klines": klines, "regime": regime_type,
                "enhanced": enhanced, "tf_metrics": tf_metrics,
                "session": self.session_filter.get_session_context()["session"],
                "features": features,
                "btc_trend": btc_trend,
                "btc_dom_change": getattr(self, '_btc_dom_change', 0),
            }
            if isinstance(score_result, dict):
                coin_data.update(score_result)

            signal = self.generate_signal(coin_data, self.config)

            # Decision Log — record WHY this signal was generated
            log_decision(
                symbol=symbol, signal=signal.get("signal", "WAIT"),
                confidence=signal.get("confidence", 0), regime=regime_type,
                composite_score=signal.get("composite_score", 50),
                reasons=signal.get("reasons", []),
                ml_confidence=signal.get("ml_confidence", 0),
                blocked=signal.get("risk_blocked", False),
                block_reason=signal.get("risk_reason", ""),
            )

            # Save features for ALL scanned coins (ML data pipeline)
            if features:
                try:
                    self.db.save_scan_features(symbol, features)
                except Exception:
                    pass

            # ML shadow mode
            if signal.get("signal") in ("LONG", "SHORT") and self.ml_engine.is_ready():
                shadow = self.ml_engine.filter_signal_shadow(features, symbol)
                signal["ml_shadow"] = shadow

            # Strategy confidence threshold filter
            if signal.get("signal") in ("LONG", "SHORT"):
                conf = signal.get("confidence", 0)
                threshold = self._strategy_conf if hasattr(self, '_strategy_conf') else 0
                if threshold > 0 and conf < threshold:
                    signal["signal"] = "WAIT"
                    signal["filtered_by_strategy"] = True
                    signal["strategy_conf_threshold"] = threshold

                # Strategy direction filter
                if hasattr(self, '_strategy_direction') and self._strategy_direction != "BOTH":
                    if signal.get("signal") != self._strategy_direction:
                        signal["signal"] = "WAIT"
                        signal["filtered_by_direction"] = True

            # Trailing TP: track per-position state
            if signal.get("signal") in ("LONG", "SHORT"):
                tp_result = self.trailing_tp.check(symbol, signal.get("pnl_pct", 0))
                if tp_result == "CLOSE_TRAILING_TP":
                    signal["signal"] = "WAIT"
                    signal["trailing_tp_close"] = True
                    logger.info(f"[EngineV2] {symbol} closed by trailing TP")
                elif tp_result == "ACTIVATE":
                    signal["trailing_tp_active"] = True

            # Risk check (only blocks if equity > 0)
            if signal.get("signal") in ("LONG", "SHORT"):
                risk = self.risk_manager.can_trade(signal)
                if not risk.get("allowed", True):
                    signal["signal"] = "WAIT"
                    signal["risk_blocked"] = True
                    signal["risk_reason"] = risk.get("reason")

                # Debate pipeline: run Bull/Bear debate for signals with sufficient confidence
                if self._debate_enabled and signal.get("confidence", 0) >= self._debate_min_confidence:
                    try:
                        debate_data = {
                            "klines_15m": klines[-20:] if klines else [],
                            "klines_1h": klines_1h[-10:] if klines_1h else [],
                            "klines_4h": klines_4h[-5:] if klines_4h else [],
                            "regime": regime_type,
                            "composite_score": score_result.get("composite_score", 50) if isinstance(score_result, dict) else 50,
                            "oi_change_24h": enhanced.get("oi_change_24h", 0),
                            "funding_rate": enhanced.get("funding_rate", 0),
                            "btc_trend": btc_trend,
                        }
                        # Get vault context for this symbol
                        vault_ctx = ""
                        try:
                            search_results = self.vault_search.search(f"{symbol} {regime_type}", limit=3)
                            if search_results.get("results"):
                                vault_ctx = "\n".join([r.get("content", "") for r in search_results["results"][:3]])
                        except Exception:
                            pass

                        debate_result = await self.debate_orchestrator.run_debate(
                            symbol=symbol,
                            data=debate_data,
                            vault_context=vault_ctx,
                        )
                        signal["debate"] = debate_result
                        signal["debate_signal"] = debate_result.get("signal", signal["signal"])
                        signal["debate_confidence"] = debate_result.get("confidence", signal.get("confidence", 50))

                        # Override quant signal if debate disagrees strongly
                        if debate_result.get("signal") == "WAIT":
                            signal["signal"] = "WAIT"
                            signal["debate_overrode"] = True
                            logger.info(f"[EngineV2] Debate overrode {symbol} → WAIT")
                        elif debate_result.get("signal") != signal.get("signal"):
                            # Check if debate confidence is higher
                            if debate_result.get("confidence", 0) > signal.get("confidence", 0) + 10:
                                signal["signal"] = debate_result["signal"]
                                signal["confidence"] = debate_result["confidence"]
                                signal["debate_overrode"] = True
                                logger.info(f"[EngineV2] Debate overrode {symbol} → {debate_result['signal']}")

                    except Exception as e:
                        logger.debug(f"[EngineV2] Debate failed for {symbol}: {e}")
                        signal["debate_error"] = str(e)

                # Risk gate check (hard + soft rules)
                try:
                    portfolio_state = {
                        "drawdown_pct": self.risk_manager.get_drawdown_pct() if hasattr(self.risk_manager, 'get_drawdown_pct') else 0,
                        "loss_streak": self.risk_manager.get_loss_streak() if hasattr(self.risk_manager, 'get_loss_streak') else 0,
                        "daily_trades": self.risk_manager.get_daily_trade_count() if hasattr(self.risk_manager, 'get_daily_trade_count') else 0,
                        "circuit_breaker_resume": self._circuit_breaker_resume if hasattr(self, '_circuit_breaker_resume') else None,
                        "correlation_exposure": getattr(self.risk_manager, 'current_drawdown', 0) / 100 if hasattr(self.risk_manager, 'current_drawdown') else 0,
                    }
                    gate_result = self.risk_gate.check(signal, portfolio_state, regime_type)
                    signal["risk_gate"] = gate_result

                    if not gate_result.get("allowed"):
                        # Both hard and soft blocks prevent trading
                        # Soft blocks CAN be overridden via LLM override mechanism
                        if gate_result.get("soft_blocked") and gate_result.get("override_available"):
                            signal["risk_gate_soft_blocked"] = True
                            signal["risk_gate_override_available"] = True
                            # Signal stays as-is but is flagged for review
                            # The override mechanism allows LLM to bypass with written reason
                        else:
                            signal["signal"] = "WAIT"
                            signal["risk_gate_blocked"] = True
                            signal["risk_gate_reason"] = gate_result.get("reason")

                except Exception as e:
                    logger.debug(f"[EngineV2] Risk gate failed for {symbol}: {e}")

                ts = datetime.now().isoformat()
                await self.db.save_signals(ts, [signal])

                signal_id = self.db.get_latest_signal_id()
                if signal_id > 0 and signal.get("signal") in ("LONG", "SHORT"):
                    if features:
                        await self.db.save_signal_features(signal_id, features)

                if self.telegram.is_ready() and signal.get("signal") in ("LONG", "SHORT"):
                    asyncio.create_task(self.telegram.send_signal_alert(signal))

            self.latest_results[symbol] = signal
            return signal
        except Exception as e:
            logger.error(f"[EngineV2] Deep scan failed for {symbol}: {e}")
            return {"ok": False, "error": str(e)}

    def _update_full_scan_snapshot(self):
        data = list(self.latest_results.values())
        data.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
        longs = sum(1 for s in data if s.get('signal') == 'LONG')
        shorts = sum(1 for s in data if s.get('signal') == 'SHORT')
        self._last_full_scan = {
            "ok": True,
            "timestamp": datetime.now().isoformat(),
            "summary": {"total": len(data), "long": longs, "short": shorts},
            "data": data[:100],
        }

    def get_latest_scan(self) -> Dict:
        if self._last_full_scan is not None:
            return self._last_full_scan
        # Fallback: build from in-memory results if scan still in progress
        if self.latest_results:
            data = list(self.latest_results.values())
            data.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
            longs = sum(1 for s in data if s.get('signal') == 'LONG')
            shorts = sum(1 for s in data if s.get('signal') == 'SHORT')
            return {"ok": True, "timestamp": datetime.now().isoformat(),
                    "summary": {"total": len(data), "long": longs, "short": shorts}, "data": data[:100]}
        return {"ok": False, "error": "No scan data. Run a scan first.", "data": []}

    def get_signals(self) -> Dict:
        scan = self.get_latest_scan()
        if not scan or not scan.get('ok'):
            return {"ok": True, "data": [], "summary": {"total": 0, "long": 0, "short": 0}}
        active = [s for s in scan.get('data', []) if s.get('signal') in ('LONG', 'SHORT')]
        active.sort(key=lambda x: x.get('composite_score', 0), reverse=True)
        return {"ok": True, "data": active, "summary": scan.get('summary', {}), "timestamp": scan.get('timestamp')}

    def get_status(self) -> Dict:
        from src.liquidation import liquidation_heatmap
        liq_summary = liquidation_heatmap.get_summary()
        now_iso = datetime.now().isoformat()
        return {
            "ok": True, "status": "online",
            "last_scan": self._last_full_scan.get("timestamp") if self._last_full_scan else now_iso,
            "next_scan": self._next_scan,
            "symbols_monitored": len(self.symbols),
            "scan_count": self._scan_count,
            "liquidation_status": liq_summary.get("status", "off"),
            "top_liq": liq_summary.get("top_liquidated", "-"),
            "server_time": now_iso,
        }

    def get_db_stats(self) -> Dict:
        return self.db.get_summary()

    def get_signals_history(self, limit: int = 100, result_filter: str = None, days: int = None) -> List[Dict]:
        return self.db.get_signals_with_outcomes(limit, result_filter, days)

    def get_daily_performance(self, days: int = 7) -> List[Dict]:
        return self.db.get_daily_performance(days)

    def get_calendar(self, year: int, month: int) -> List[Dict]:
        return self.db.get_calendar_month(year, month)

    @property
    def rl_optimizer(self):
        return self.ml_engine

    @property
    def session_filter(self):
        class _SessionStub:
            def get_session_context(self):
                from datetime import timezone
                h = datetime.now(timezone.utc).hour
                if 13 <= h < 16: return {"session": "LONDON/NY OVERLAP", "bias": "HIGH_VOL", "utc_hour": h}
                elif 8 <= h < 13: return {"session": "LONDON", "bias": "TRENDING", "utc_hour": h}
                elif 16 <= h < 22: return {"session": "NEW YORK", "bias": "TRENDING", "utc_hour": h}
                elif 0 <= h < 8: return {"session": "ASIA", "bias": "RANGING", "utc_hour": h}
                return {"session": "OFF-HOURS", "bias": "LOW_VOL", "utc_hour": h}
        return _SessionStub()

    def _get_hours_since_last_signal(self, symbol: str) -> float:
        try:
            c = self.db.conn.cursor()
            c.execute("SELECT timestamp FROM signals WHERE symbol=? ORDER BY id DESC LIMIT 1", (symbol,))
            row = c.fetchone()
            if row and row["timestamp"]:
                try:
                    last_ts = datetime.fromisoformat(row["timestamp"])
                    return round((datetime.now() - last_ts).total_seconds() / 3600, 2)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"hours_since error: {e}")
        return 24.0

    def _validate_symbols(self):
        try:
            valid = self.api.get_all_symbols()
            if not valid:
                logger.warning("[EngineV2] Could not fetch active symbols — skipping validation")
                return
            invalid = [s for s in self.symbols if s not in valid]
            if invalid:
                logger.warning(f"[EngineV2] Removing {len(invalid)} invalid symbols: {invalid}")
                self.symbols = [s for s in self.symbols if s in valid]
            logger.info(f"[EngineV2] Validated {len(self.symbols)} symbols")
        except Exception as e:
            logger.warning(f"[EngineV2] Symbol validation failed: {e}")

    async def train_ml_model(self, lookback_days: int = 60) -> Dict:
        result = self.ml_engine.train_with_walkforward(lookback_days)
        status = self.ml_engine.get_status()
        await self.db.save_ml_model_meta(status)
        if result.get("status") == "deployed":
            fold_wrs = result.get("fold_wrs", [])
            if fold_wrs:
                self.risk_manager.check_overfit_walkforward(fold_wrs, result.get("samples", 0))
        return result

    async def run_threshold_evolution(self, auto_apply: bool = False) -> Dict:
        """Run threshold evolution cycle based on recent performance."""
        from src.threshold_evolution import get_evolution_engine
        
        try:
            engine = get_evolution_engine(self.db.db_path)
            result = engine.run_evolution_cycle(auto_apply=auto_apply)
            
            if result.get('applied'):
                logger.info("[EngineV2] Threshold evolution applied changes")
                if self.telegram.is_ready():
                    await self.telegram.send_message(
                        f"🧬 Threshold Evolution Applied\n"
                        f"Changes: {len(result.get('recommendations', {}).get('changes', {}))}\n"
                        f"New blocks: {len(result.get('recommendations', {}).get('blocks', []))}"
                    )
            else:
                logger.info(f"[EngineV2] Threshold evolution: {result.get('reason', 'No changes')}")
            
            return result
            
        except Exception as e:
            logger.error(f"[EngineV2] Threshold evolution error: {e}")
            return {"error": str(e)}

    def clear_cache(self):
        self.latest_results = {}
        self._last_full_scan = None
        logger.info("[EngineV2] Cache cleared")

    def refresh_universe(self) -> List[str]:
        """Force refresh dynamic universe."""
        new_symbols = self.universe.select_universe(force_refresh=True)
        if new_symbols:
            self.symbols = new_symbols
            logger.info(f"[EngineV2] Universe refreshed: {len(self.symbols)} symbols")
        return self.symbols

    def get_universe_info(self) -> Dict:
        return {
            "dynamic": self._use_dynamic_universe,
            "symbols": len(self.symbols),
            "stats": self.universe.get_universe_stats(),
            "blacklist": self.universe.get_blacklist(),
        }

    def get_market_regime_info(self) -> Dict:
        return self._market_regime_data

    def get_debate_stats(self) -> Dict:
        """Return debate pipeline statistics."""
        return {
            "enabled": self._debate_enabled,
            "orchestrator": self.debate_orchestrator.get_debate_stats(),
            "risk_gate": self.risk_gate.get_status(),
            "bull_ready": self.bull_agent.is_ready(),
            "bear_ready": self.bear_agent.is_ready(),
            "synth_ready": self.synthesizer.is_ready(),
        }

    async def _run_learning_loop(self, current_signals: List[Dict]):
        """Use REAL trade outcomes from DB (WIN/LOSS with actual PnL) for learning.
        Feeds into vault skills, lessons, memory, curator, and daily_goal."""
        # Fetch recently closed signals with real outcomes from DB
        try:
            c = self.db.conn.cursor()
            c.execute(
                """SELECT s.symbol, s.signal, s.result, s.pnl_pct, s.regime,
                          s.confidence, s.timestamp, s.exit_timestamp, s.exit_reason
                   FROM signals s
                   WHERE s.result IN ('WIN', 'LOSS')
                     AND s.exit_timestamp IS NOT NULL
                     AND s.id NOT IN (
                         SELECT DISTINCT signal_id FROM signal_features sf
                         WHERE sf.learning_processed = 1
                     )
                   ORDER BY s.exit_timestamp DESC LIMIT 20""",
            )
            rows = c.fetchall()
        except Exception as e:
            logger.debug(f"[EngineV2] Learning loop DB query: {e}")
            rows = []

        if not rows:
            return

        outcomes = []
        for row in rows:
            outcomes.append({
                "symbol": row["symbol"],
                "signal": row["signal"],
                "result": row["result"],
                "pnl_pct": round(row["pnl_pct"] or 0, 2),
                "regime": row["regime"] or "UNKNOWN",
                "confidence": row["confidence"] or 50,
                "exit_reason": row.get("exit_reason", ""),
                "timestamp": row["timestamp"],
            })
            # Mark as learning-processed
            try:
                c.execute(
                    """INSERT OR IGNORE INTO signal_features (signal_id, learning_processed)
                       VALUES (?, 1)""",
                    (row.rowid if hasattr(row, "rowid") else 0,),
                )
            except Exception:
                pass

        # Feed outcomes into daily_goal (use real PnL)
        if outcomes:
            try:
                from src.daily_goal import DailyGoal
                goal = DailyGoal()
                total_pnl = sum(o["pnl_pct"] for o in outcomes)
                goal.update(total_pnl)
            except Exception as e:
                logger.debug(f"[EngineV2] daily_goal update: {e}")

        # Create lessons in vault for each outcome
        for o in outcomes:
            try:
                self.vault_lessons.create_lesson(
                    symbol=o["symbol"],
                    regime=o["regime"],
                    signal=o["signal"],
                    result=o["result"],
                    pnl_pct=o["pnl_pct"],
                    confidence=o["confidence"],
                    exit_reason=o.get("exit_reason", ""),
                )
            except Exception as e:
                logger.debug(f"[EngineV2] Lesson creation: {e}")

        # Pattern detection: 3+ similar outcomes → create skill in vault
        if len(outcomes) >= 3:
            wins = [o for o in outcomes if o["result"] == "WIN"]
            losses = [o for o in outcomes if o["result"] == "LOSS"]
            if len(wins) >= 3:
                regime_counts = {}
                for o in wins:
                    r = o.get("regime", "ANY")
                    regime_counts[r] = regime_counts.get(r, 0) + 1
                dominant_regime = max(regime_counts, key=regime_counts.get)
                try:
                    self.vault_skills.create_skill(
                        name=f"winning_{dominant_regime.lower()}_pattern",
                        description=f"Detected winning pattern in {dominant_regime} regime ({len(wins)} recent wins)",
                        tags=["auto", "pattern", "positive"],
                        regime=dominant_regime,
                        signal=wins[0]["signal"],
                        procedure="Enter on high-confidence signals matching this regime+signal combo",
                        pitfalls="Avoid when BTC dominance diverges sharply",
                        evidence=f"WR: {len(wins)}W/{len(outcomes)} total, avg PnL: {sum(o['pnl_pct'] for o in wins)/len(wins):+.2f}%",
                    )
                    logger.info(f"[EngineV2] Vault skill created for {dominant_regime} pattern")
                    # Swarm auto-push — ANONYMIZED (NO PnL amounts)
                    try:
                        from src.hivemind_client import get_hivemind
                        hive = get_hivemind()
                        if hive and hive.enabled:
                            avg_conf = sum(o["confidence"] for o in wins) / len(wins)
                            hive.push_lesson(
                                rule=f"winning_{dominant_regime.lower()}_pattern",
                                tags=["auto", "pattern", "positive"],
                                regime=dominant_regime,
                                signal=wins[0]["signal"],
                                result="WIN",
                                pnl_pct=0.0,  # ANONYMIZED — never share real PnL
                                confidence=avg_conf,
                            )
                    except Exception:
                        pass
                except Exception as e:
                    logger.debug(f"[EngineV2] Skill creation: {e}")
            if len(losses) >= 3:
                regime_counts = {}
                for o in losses:
                    r = o.get("regime", "ANY")
                    regime_counts[r] = regime_counts.get(r, 0) + 1
                dominant_regime = max(regime_counts, key=regime_counts.get)
                try:
                    self.vault_skills.create_skill(
                        name=f"avoid_{dominant_regime.lower()}_signals",
                        description=f"High loss rate detected in {dominant_regime} regime ({len(losses)} losses)",
                        tags=["auto", "pattern", "warning"],
                        regime=dominant_regime,
                        signal=losses[0]["signal"],
                        procedure="Reduce confidence threshold or filter out this regime+signal combo",
                        pitfalls="This pattern may indicate regime change or model drift",
                        evidence=f"LR: {len(losses)}L/{len(outcomes)} total, avg PnL: {sum(o['pnl_pct'] for o in losses)/len(losses):+.2f}%",
                    )
                    logger.info(f"[EngineV2] Vault warning skill created for {dominant_regime}")
                    # Swarm auto-push — ANONYMIZED (NO PnL amounts)
                    try:
                        from src.hivemind_client import get_hivemind
                        hive = get_hivemind()
                        if hive and hive.enabled:
                            avg_conf = sum(o["confidence"] for o in losses) / len(losses)
                            hive.push_lesson(
                                rule=f"avoid_{dominant_regime.lower()}_signals",
                                tags=["auto", "pattern", "warning"],
                                regime=dominant_regime,
                                signal=losses[0]["signal"],
                                result="LOSS",
                                pnl_pct=0.0,  # ANONYMIZED — never share real PnL
                                confidence=avg_conf,
                            )
                    except Exception:
                        pass
                except Exception as e:
                    logger.debug(f"[EngineV2] Warning skill: {e}")

        # Vault memory: add entry for each outcome
        if outcomes:
            try:
                for o in outcomes[:5]:
                    entry = (
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] "
                        f"{o['symbol']} {o['signal']}: {o['result']} "
                        f"({o['pnl_pct']:+.2f}%, regime={o['regime']}, "
                        f"conf={o['confidence']})"
                    )
                    self.vault_memory.add_entry(entry)
                    # Auto-consolidate if near limit
                    usage = self.vault_memory.get_usage()
                    if usage["percent_used"] > 80:
                        self.vault_memory.consolidate()
            except Exception as e:
                logger.debug(f"[EngineV2] Vault memory: {e}")

        # Curator: nudge every 10 trades
        self._trade_count_since_nudge += len(outcomes)
        if self._trade_count_since_nudge >= 10:
            try:
                from src.curator import Curator
                curator = Curator()
                if curator.should_nudge():
                    prompt = curator.generate_nudge_prompt()
                    logger.info(f"[EngineV2] Curator nudge generated ({len(prompt)} chars)")
                    self._trade_count_since_nudge = 0
            except Exception as e:
                logger.debug(f"[EngineV2] curator nudge: {e}")

        # Re-index vault after learning
        try:
            self.vault_indexer.index_all()
        except Exception as e:
            logger.debug(f"[EngineV2] Vault re-index: {e}")

    def set_hivemind_client(self, hivemind):
        """Set HiveMind client on the ReAct executor for swarm tool access."""
        if self.react_executor:
            self.react_executor.hivemind = hivemind

    async def run_react_screening(self, portfolio: Dict = None) -> Dict:
        """Run a screening cycle via ReAct agent (LLM orchestrator)."""
        if not self.react_agent:
            return {"ok": False, "error": "ReAct agent not initialized"}

        # Get lessons for context
        lessons_text = None
        try:
            from src.agents.lesson_deriver import derive_lesson
            from src.vault.lesson_manager import LessonManager
            lm = LessonManager(self.db.conn)
            recent = lm.get_recent(limit=10)
            if recent:
                lessons_text = "\n".join([
                    f"- [{l.get('outcome', '?')}] {l.get('rule', '')[:200]}"
                    for l in recent
                ])
        except Exception:
            pass

        # Get swarm lessons
        swarm_text = None
        try:
            from src.hivemind_client import get_hivemind
            hive = get_hivemind()
            if hive and hive.is_enabled():
                swarm_lessons = hive.pull_lessons(limit=6)
                if swarm_lessons:
                    swarm_text = "\n".join([
                        f"- [HIVEMIND] {l.get('rule', '')[:200]}"
                        for l in swarm_lessons
                    ])
        except Exception:
            pass

        from src.agents.react_loop import run_screening
        result = await run_screening(
            agent=self.react_agent,
            portfolio=portfolio,
            lessons=lessons_text,
            swarm_lessons=swarm_text,
        )

        # Log decision
        if result.get("tool_calls"):
            for tc in result["tool_calls"]:
                if tc["tool"] == "open_position" and tc.get("success"):
                    from src.agents.decision_log import append_decision
                    append_decision(
                        decision_type="open",
                        actor="REACT_SCREENER",
                        symbol=tc["args"].get("symbol", ""),
                        summary=tc["args"].get("reason", "")[:200],
                        signal=tc["args"].get("signal", ""),
                        confidence=tc["args"].get("confidence", 50),
                    )

        return {"ok": True, "react": result}

    async def run_react_management(self, portfolio: Dict = None) -> Dict:
        """Run a management cycle via ReAct agent (LLM orchestrator)."""
        if not self.react_agent:
            return {"ok": False, "error": "ReAct agent not initialized"}

        # Get open positions
        positions = []
        try:
            c = self.db.conn.cursor()
            c.execute("SELECT * FROM signals WHERE status='open' ORDER BY timestamp DESC")
            positions = [dict(r) for r in c.fetchall()]
        except Exception:
            pass

        if not positions:
            return {"ok": True, "react": {"content": "No open positions to manage", "steps": 0}}

        from src.agents.react_loop import run_management
        result = await run_management(
            agent=self.react_agent,
            portfolio=portfolio,
            positions=positions,
        )

        return {"ok": True, "react": result}

    async def process_outcome_with_react(self, outcomes: List[Dict]):
        """Process trade outcomes: derive lessons, update skills, evolve thresholds."""
        if not outcomes:
            return

        # Auto-derive lessons from outcomes
        from src.agents.lesson_deriver import derive_lesson
        from src.vault.lesson_manager import LessonManager
        from src.agents.symbol_memory import record_trade
        from src.agents.skill_engine import update_skill_performance

        lm = LessonManager(self.db.conn)
        for o in outcomes:
            # Derive lesson
            lesson = derive_lesson(o)
            if lesson:
                lm.add_lesson(
                    lesson["rule"],
                    lesson.get("tags", []),
                    outcome=lesson.get("outcome", "manual"),
                )
                logger.info(f"[EngineV2] Auto-lesson: {lesson['rule'][:100]}")

            # Update symbol memory
            record_trade(
                symbol=o.get("symbol", ""),
                signal=o.get("signal", ""),
                regime=o.get("regime", ""),
                result=o.get("result", ""),
                pnl_pct=o.get("pnl_pct", 0),
                confidence=o.get("confidence", 50),
                held_hours=o.get("held_hours", 0),
                exit_reason=o.get("exit_reason", ""),
                reason=o.get("reason", ""),
            )

        # Evolve thresholds
        try:
            c = self.db.conn.cursor()
            c.execute(
                "SELECT symbol, signal, regime, result, pnl, confidence FROM signals "
                "WHERE status='closed' AND pnl IS NOT NULL ORDER BY timestamp DESC LIMIT 100"
            )
            trades = []
            for r in c.fetchall():
                trades.append({
                    "symbol": r[0], "signal": r[1], "regime": r[2],
                    "result": r[3], "pnl_pct": r[4] or 0, "confidence": r[5] or 50,
                })
            if len(trades) >= 10:
                from src.agents.threshold_evolution import evolve_thresholds
                evolution = evolve_thresholds(trades)
                if evolution:
                    logger.info(f"[EngineV2] Thresholds evolved: {evolution['rationale']}")
        except Exception as e:
            logger.debug(f"[EngineV2] Threshold evolution error: {e}")

        # Update skill performance
        try:
            from src.agents.skill_engine import get_active_skill
            active = get_active_skill()
            if active:
                for o in outcomes:
                    update_skill_performance(
                        active["name"],
                        o.get("result", ""),
                        o.get("pnl_pct", 0),
                    )
        except Exception as e:
            logger.debug(f"[EngineV2] Skill update error: {e}")

    def close(self):
        pass
