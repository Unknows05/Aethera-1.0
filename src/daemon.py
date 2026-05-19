"""
Aethera Daemon — Background autonomous process.
Runs screening, management, and reflection cycles 24/7.
Survives TUI close, recovers after reboot.
"""
import os
import sys
import json
import signal
import asyncio
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

# Add project root to path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Load .env if not already loaded
_env_path = SCRIPT_DIR.parent / ".env"
if _env_path.exists():
    try:
        with open(_env_path) as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("\"'")
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

from src.config_loader import get_config
from src.engine_v2 import ScreeningEngineV2
from src.agents.scheduler import AgentScheduler
from src.agents.health import HealthMonitor
from src.agents.data_collector import DataCollector
from src.agents.bull_agent import BullAgent
from src.agents.bear_agent import BearAgent
from src.agents.synthesizer import Synthesizer
from src.agents.debate import DebateOrchestrator
from src.agents.risk_gate import RiskGate
from src.agents.screening_agent import ScreeningAgent
from src.agents.management_agent import ManagementAgent
from src.agents.reflection import ReflectionAgent

logger = logging.getLogger(__name__)

PID_FILE = "data/aethera.pid"
STATE_FILE = "data/daemon_state.json"


class AetheraDaemon:
    """Main daemon process — runs autonomous trading cycles."""

    def __init__(self, config: Dict = None, trade_enabled: bool = False):
        self.config = config or get_config()
        self.trade_enabled = trade_enabled
        self.engine: Optional[ScreeningEngineV2] = None
        self.scheduler: Optional[AgentScheduler] = None
        self.health: Optional[HealthMonitor] = None
        self.data_collector: Optional[DataCollector] = None
        self.screening_agent: Optional[ScreeningAgent] = None
        self.management_agent: Optional[ManagementAgent] = None
        self.reflection_agent: Optional[ReflectionAgent] = None
        self._state: Dict = {}
        self._shutdown = False

    def load_state(self):
        """Load previous daemon state from file."""
        if Path(STATE_FILE).exists():
            try:
                with open(STATE_FILE) as f:
                    self._state = json.load(f)
                logger.info(f"[Daemon] State loaded: {self._state.get('last_screening', 'never')}")
            except Exception as e:
                logger.warning(f"[Daemon] State load failed: {e}")
                self._state = {}
        else:
            self._state = {}

    def save_state(self):
        """Save current daemon state to file."""
        try:
            Path("data").mkdir(exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.error(f"[Daemon] State save failed: {e}")

    def write_pid(self):
        """Write PID file for process management."""
        Path("data").mkdir(exist_ok=True)
        Path(PID_FILE).write_text(str(os.getpid()))

    def remove_pid(self):
        """Remove PID file on shutdown."""
        try:
            Path(PID_FILE).unlink(missing_ok=True)
        except Exception:
            pass

    async def start(self):
        """Start the daemon — initialize all components and begin cycles."""
        logger.info("[Daemon] Starting Aethera v1.5...")

        # Load previous state
        self.load_state()

        # Initialize engine
        self.engine = ScreeningEngineV2(self.config, cache_dir="data")
        logger.info("[Daemon] Engine initialized")

        # Initialize debate pipeline (Phase 3)
        llm_config = self.config.get("llm", {})
        llm_api_key = llm_config.get("api_key", "") or os.getenv("OPENROUTER_API_KEY", "")
        llm_model = llm_config.get("model", "deepseek/deepseek-chat-v4:free")
        llm_base_url = llm_config.get("base_url", "https://openrouter.ai/api/v1")

        self.bull_agent = BullAgent(model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        self.bear_agent = BearAgent(model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        self.synthesizer = Synthesizer(model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        self.debate_orchestrator = DebateOrchestrator(
            bull_agent=self.bull_agent,
            bear_agent=self.bear_agent,
            synthesizer=self.synthesizer,
        )
        self.risk_gate = RiskGate(self.config.get("risk_gate", {}))
        logger.info("[Daemon] Debate pipeline initialized")

        # Initialize screening agent (Phase 2)
        self.screening_agent = ScreeningAgent(
            model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        logger.info("[Daemon] Screening agent initialized")

        # Initialize management agent (Phase 2)
        self.management_agent = ManagementAgent(
            model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        logger.info("[Daemon] Management agent initialized")

        # Initialize reflection agent (Phase 2)
        self.reflection_agent = ReflectionAgent(
            model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
        logger.info("[Daemon] Reflection agent initialized")

        # Initialize data collector
        self.data_collector = DataCollector(api=self.engine.api)

        # Initialize health monitor
        self.health = HealthMonitor(
            engine=self.engine,
            telegram=self.engine.telegram,
            state=self._state,
        )

        # Initialize scheduler
        self.scheduler = AgentScheduler()

        # Write PID
        self.write_pid()

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        # Start scheduler
        await self.scheduler.start(
            screening_fn=self._screening_cycle,
            management_fn=self._management_cycle,
            reflection_fn=self._reflection_cycle,
            health_fn=self._health_check,
            screening_interval=self.config.get("screening_interval", 900),
            management_interval=self.config.get("management_interval", 300),
            reflection_interval=self.config.get("reflection_interval", 3600),
            health_interval=60,
        )

        logger.info("[Daemon] All cycles started — running autonomously")

        # Keep running until shutdown
        while not self._shutdown:
            await asyncio.sleep(1)

    async def shutdown(self):
        """Graceful shutdown — stop cycles, save state, close connections."""
        if self._shutdown:
            return
        self._shutdown = True

        logger.info("[Daemon] Shutting down...")

        # Stop scheduler
        if self.scheduler:
            await self.scheduler.stop()

        # Save state
        self._state["last_shutdown"] = datetime.now().isoformat()
        self.save_state()

        # Close engine
        if self.engine:
            self.engine.close()

        # Remove PID
        self.remove_pid()

        logger.info("[Daemon] Shutdown complete")

    async def _screening_cycle(self):
        """Run screening cycle — scan market, generate signals."""
        try:
            logger.info("[Daemon] Screening cycle started")

            # Run engine scan (existing path)
            result = await self.engine.scan()

            # Run screening agent for additional LLM-guided selection
            if self.screening_agent and self.screening_agent.is_ready():
                try:
                    regime_data = self.engine._market_regime_data
                    agent_result = await self.screening_agent.run_cycle(
                        engine=self.engine,
                        data_collector=self.data_collector,
                        debate_orchestrator=self.debate_orchestrator,
                        risk_gate=self.risk_gate,
                        vault_search=self.engine.vault_search if hasattr(self.engine, 'vault_search') else None,
                        vault_lessons=self.engine.vault_lessons if hasattr(self.engine, 'vault_lessons') else None,
                        market_regime_data=regime_data,
                    )
                    logger.info(f"[Daemon] Screening agent: {agent_result.get('summary', {})}")
                except Exception as e:
                    logger.debug(f"[Daemon] Screening agent error: {e}")

            self._state["last_screening"] = datetime.now().isoformat()
            self._state["screening_count"] = self._state.get("screening_count", 0) + 1

            if result.get("ok"):
                summary = result.get("summary", {})
                logger.info(f"[Daemon] Screening done: {summary}")
            else:
                logger.warning(f"[Daemon] Screening failed: {result.get('error')}")

            self.save_state()
        except Exception as e:
            logger.error(f"[Daemon] Screening cycle error: {e}")

    async def _management_cycle(self):
        """Run management cycle — check positions, make decisions."""
        try:
            if not self.trade_enabled:
                return

            logger.debug("[Daemon] Management cycle started")

            # Get open positions
            try:
                positions = self.engine.position_manager.get_open_positions()
            except Exception:
                positions = []

            if not positions:
                return

            # Fetch current prices
            loop = asyncio.get_event_loop()
            ticker_data = await loop.run_in_executor(None, self.engine.api.get_24h_ticker)
            prices = {t["symbol"]: float(t["lastPrice"]) for t in ticker_data}

            # Manage positions — deterministic rules
            regime = self.engine._get_current_market_regime()
            actions_det = self.engine.position_manager.manage_all(prices, regime)

            for action in actions_det:
                logger.info(f"[Daemon] Position action: {action}")

            # Management agent — LLM-guided decisions
            if self.management_agent and self.management_agent.is_ready():
                try:
                    decisions = await self.management_agent.manage(
                        positions=positions,
                        prices=prices,
                        regime=regime,
                        engine=self.engine,
                        vault_search=self.engine.vault_search if hasattr(self.engine, 'vault_search') else None,
                    )
                    for d in decisions:
                        if d["action"] != "STAY":
                            logger.info(f"[Daemon] Management: {d['symbol']} → {d['action']} ({d['reason']})")
                except Exception as e:
                    logger.debug(f"[Daemon] Management agent error: {e}")

            self._state["last_management"] = datetime.now().isoformat()
            self._state["management_count"] = self._state.get("management_count", 0) + 1
            self.save_state()

        except Exception as e:
            logger.error(f"[Daemon] Management cycle error: {e}")

    async def _reflection_cycle(self):
        """Run reflection cycle — learn, evolve, sync swarm."""
        try:
            logger.info("[Daemon] Reflection cycle started")

            # Run learning loop
            signals = self.engine.get_signals().get("data", [])
            await self.engine._run_learning_loop(signals)

            # Re-index vault
            try:
                self.engine.vault_indexer.index_all()
            except Exception as e:
                logger.debug(f"[Daemon] Vault re-index: {e}")

            # Evolve thresholds (if enough data)
            try:
                await self.engine.run_threshold_evolution(auto_apply=False)
            except Exception as e:
                logger.debug(f"[Daemon] Threshold evolution: {e}")

            # Reflection agent — LLM-guided learning
            if self.reflection_agent and self.reflection_agent.is_ready():
                try:
                    agent_result = await self.reflection_agent.reflect(
                        engine=self.engine,
                        vault_skills=self.engine.vault_skills if hasattr(self.engine, 'vault_skills') else None,
                        vault_lessons=self.engine.vault_lessons if hasattr(self.engine, 'vault_lessons') else None,
                        vault_memory=self.engine.vault_memory if hasattr(self.engine, 'vault_memory') else None,
                        vault_indexer=self.engine.vault_indexer if hasattr(self.engine, 'vault_indexer') else None,
                        vault_search=self.engine.vault_search if hasattr(self.engine, 'vault_search') else None,
                        hivemind_client=getattr(self.engine, 'hivemind_client', None),
                    )
                    logger.info(f"[Daemon] Reflection agent: {agent_result.get('skills_created', [])}")
                except Exception as e:
                    logger.debug(f"[Daemon] Reflection agent error: {e}")

            self._state["last_reflection"] = datetime.now().isoformat()
            self._state["reflection_count"] = self._state.get("reflection_count", 0) + 1
            self.save_state()

            logger.info("[Daemon] Reflection cycle done")
        except Exception as e:
            logger.error(f"[Daemon] Reflection cycle error: {e}")

    async def _health_check(self):
        """Run health check — monitor system health."""
        try:
            if self.health:
                await self.health.check()
        except Exception as e:
            logger.error(f"[Daemon] Health check error: {e}")


async def run_daemon(trade_enabled: bool = False):
    """Entry point for daemon process."""
    # Setup logging
    Path("data").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("data/daemon.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    config = get_config()
    daemon = AetheraDaemon(config=config, trade_enabled=trade_enabled)

    try:
        await daemon.start()
    except KeyboardInterrupt:
        await daemon.shutdown()
    except Exception as e:
        logger.error(f"[Daemon] Fatal error: {e}")
        await daemon.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    trade = "--trade" in sys.argv
    asyncio.run(run_daemon(trade_enabled=trade))
