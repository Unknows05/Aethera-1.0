"""
Session filter with reduced penalty.
"""
import logging
import time

logger = logging.getLogger(__name__)

class SessionFilter:
    def __init__(self, config=None):
        self.config = config or {}
        # Reduced penalty from 0.15 to 0.08
        self.penalty = self.config.get('session_penalty', 0.08)
        self.session_window = 3600  # 1 hour
        self.last_reset = time.time()
        logger.info("[SessionFilter] Initialized")
    
    def apply_penalty(self, signals: list) -> list:
        """Apply session-based penalty to signals."""
        current = time.time()
        if current - self.last_reset > self.session_window:
            self.last_reset = current
        
        adjusted = []
        for s in signals:
            score = s.get('score', 50)
            # Reduce penalty impact
            adjusted_score = score - self.penalty * 100
            s['adjusted_score'] = max(0, min(100, adjusted_score))
            adjusted.append(s)
        return adjusted
