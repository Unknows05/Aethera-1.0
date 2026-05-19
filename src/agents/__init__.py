"""
Aethera Autonomous Agents — Data Collection, Debate, Risk, Health, Scheduler,
Screening, Management, Reflection.
"""
from src.agents.data_collector import DataCollector
from src.agents.health import HealthMonitor
from src.agents.scheduler import AgentScheduler
from src.agents.bull_agent import BullAgent
from src.agents.bear_agent import BearAgent
from src.agents.synthesizer import Synthesizer
from src.agents.debate import DebateOrchestrator
from src.agents.risk_gate import RiskGate
from src.agents.screening_agent import ScreeningAgent
from src.agents.management_agent import ManagementAgent
from src.agents.reflection import ReflectionAgent

__all__ = [
    "DataCollector",
    "HealthMonitor",
    "AgentScheduler",
    "BullAgent",
    "BearAgent",
    "Synthesizer",
    "DebateOrchestrator",
    "RiskGate",
    "ScreeningAgent",
    "ManagementAgent",
    "ReflectionAgent",
]
