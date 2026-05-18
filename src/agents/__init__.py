"""
Aethera Autonomous Agents — Data Collection, Debate, Risk, Health, Scheduler.
"""
from src.agents.data_collector import DataCollector
from src.agents.health import HealthMonitor
from src.agents.scheduler import AgentScheduler
from src.agents.bull_agent import BullAgent
from src.agents.bear_agent import BearAgent
from src.agents.synthesizer import Synthesizer
from src.agents.debate import DebateOrchestrator
from src.agents.risk_gate import RiskGate

__all__ = [
    "DataCollector",
    "HealthMonitor",
    "AgentScheduler",
    "BullAgent",
    "BearAgent",
    "Synthesizer",
    "DebateOrchestrator",
    "RiskGate",
]
