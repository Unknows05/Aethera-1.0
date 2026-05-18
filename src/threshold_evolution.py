"""
Threshold Evolution — auto-adjust screening criteria based on performance.
Adopted from Meridian's evolveThresholds() with enhancements.

Analyzes closed position performance and evolves:
1. Confidence thresholds
2. Regime+signal blocks
3. Min confidence per regime
"""
import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class PerformanceRecord:
    """Structure for closed trade performance data."""
    symbol: str
    regime: str
    signal: str  # LONG or SHORT
    result: str  # WIN or LOSS
    pnl_pct: float
    confidence: float
    composite_score: float
    entry_price: float
    exit_price: float
    held_hours: float
    close_reason: str
    timestamp: str


class ThresholdEvolution:
    """
    Adaptive threshold evolution engine.
    Learns from closed trades and adjusts system parameters.
    """
    
    MIN_SAMPLES = 5  # Minimum closed trades before evolution
    MAX_CHANGE_PER_STEP = 0.20  # Max 20% change per evolution
    CONFIDENCE_FLOOR = 45  # Absolute minimum confidence
    CONFIDENCE_CEILING = 75  # Absolute maximum confidence
    
    def __init__(self, db_path: str = "data/screener.db"):
        self.db_path = db_path
        self.evolution_history: List[Dict] = []
        
    def analyze_performance(self, performance_data: List[PerformanceRecord]) -> Dict:
        """
        Analyze closed trades and calculate evolution recommendations.
        
        Returns:
            {
                'changes': Dict of parameter changes,
                'rationale': Dict of explanations,
                'blocks': List of new regime+signal blocks,
                'unblocks': List of regime+signal combos to unblock
            }
        """
        if len(performance_data) < self.MIN_SAMPLES:
            return {
                'changes': {},
                'rationale': {},
                'blocks': [],
                'unblocks': [],
                'reason': f'Insufficient data: {len(performance_data)}/{self.MIN_SAMPLES} samples'
            }
        
        winners = [p for p in performance_data if p.pnl_pct > 0]
        losers = [p for p in performance_data if p.pnl_pct < -5]
        
        changes = {}
        rationale = {}
        new_blocks = []
        unblocks = []
        
        # 1. Evolve global confidence threshold
        if losers:
            avg_loser_conf = sum(l.confidence for l in losers) / len(losers)
            if avg_loser_conf < 50:
                # Losers had low confidence → raise minimum
                current_min = self._get_current_min_confidence()
                target = max(current_min + 5, 55)
                new_min = min(target, self.CONFIDENCE_CEILING)
                if new_min > current_min:
                    changes['min_confidence'] = new_min
                    rationale['min_confidence'] = f'Losers avg confidence {avg_loser_conf:.0f}% → raise minimum to {new_min}%'
        
        if winners and len(winners) >= 3:
            avg_winner_conf = sum(w.confidence for w in winners) / len(winners)
            if avg_winner_conf > 70:
                # Winners have high confidence → can lower threshold slightly for more opportunities
                current_min = self._get_current_min_confidence()
                if current_min > 50:
                    new_min = max(current_min - 3, self.CONFIDENCE_FLOOR)
                    if new_min < current_min:
                        changes['min_confidence'] = new_min
                        rationale['min_confidence'] = f'Winners avg confidence {avg_winner_conf:.0f}% → lower minimum to {new_min}% for more opportunities'
        
        # 2. Analyze regime+signal combinations
        combo_stats = self._analyze_combos(performance_data)
        
        for combo, stats in combo_stats.items():
            regime, signal = combo
            wr = stats['win_rate']
            n = stats['count']
            avg_pnl = stats['avg_pnl']
            
            # Block combos with WR < 40% and sufficient samples
            if n >= 5 and wr < 40:
                new_blocks.append({
                    'regime': regime,
                    'signal': signal,
                    'wr': wr,
                    'n': n,
                    'avg_pnl': avg_pnl,
                    'reason': f'{regime}+{signal}: WR {wr:.1f}% (n={n}) — auto-blocked'
                })
            
            # Unblock combos with WR > 55% (recovery detection)
            if n >= 10 and wr > 55:
                unblocks.append({
                    'regime': regime,
                    'signal': signal,
                    'wr': wr,
                    'n': n,
                    'reason': f'{regime}+{signal}: WR recovered to {wr:.1f}% (n={n})'
                })
            
            # Evolve confidence per combo
            if n >= 5:
                if wr < 45:
                    # Poor combo → require higher confidence
                    key = f'min_conf_{regime}_{signal}'
                    current = self._get_combo_min_conf(regime, signal)
                    new_min = min(current + 5, self.CONFIDENCE_CEILING)
                    if new_min > current:
                        changes[key] = new_min
                        rationale[key] = f'{regime}+{signal} WR {wr:.1f}% → raise min confidence to {new_min}%'
                elif wr > 60:
                    # Good combo → can lower confidence requirement
                    key = f'min_conf_{regime}_{signal}'
                    current = self._get_combo_min_conf(regime, signal)
                    new_min = max(current - 3, self.CONFIDENCE_FLOOR)
                    if new_min < current:
                        changes[key] = new_min
                        rationale[key] = f'{regime}+{signal} WR {wr:.1f}% → lower min confidence to {new_min}%'
        
        # 3. Score threshold evolution
        if performance_data:
            avg_score_winners = sum(p.composite_score for p in winners) / len(winners) if winners else 0
            avg_score_losers = sum(p.composite_score for p in losers) / len(losers) if losers else 0
            
            if avg_score_losers > 0 and avg_score_winners > 0:
                if avg_score_losers > 55:
                    # Losers entered with decent scores → raise entry threshold
                    current_long = self._get_score_threshold('LONG')
                    if current_long < 65:
                        new_threshold = min(current_long + 3, 70)
                        changes['score_threshold_long'] = new_threshold
                        rationale['score_threshold_long'] = f'Losers avg score {avg_score_losers:.0f} → raise LONG threshold to {new_threshold}'
        
        return {
            'changes': changes,
            'rationale': rationale,
            'blocks': new_blocks,
            'unblocks': unblocks,
            'stats': {
                'total_trades': len(performance_data),
                'winners': len(winners),
                'losers': len(losers),
                'overall_wr': len(winners) / len(performance_data) * 100 if performance_data else 0
            }
        }
    
    def _analyze_combos(self, performance_data: List[PerformanceRecord]) -> Dict:
        """Analyze performance per regime+signal combination."""
        combos = {}
        
        for p in performance_data:
            key = (p.regime, p.signal)
            if key not in combos:
                combos[key] = {
                    'trades': [],
                    'wins': 0,
                    'total_pnl': 0,
                    'count': 0
                }
            
            combos[key]['trades'].append(p)
            combos[key]['count'] += 1
            if p.pnl_pct > 0:
                combos[key]['wins'] += 1
            combos[key]['total_pnl'] += p.pnl_pct
        
        # Calculate stats
        result = {}
        for key, data in combos.items():
            result[key] = {
                'count': data['count'],
                'win_rate': (data['wins'] / data['count'] * 100) if data['count'] > 0 else 0,
                'avg_pnl': (data['total_pnl'] / data['count']) if data['count'] > 0 else 0,
                'total_pnl': data['total_pnl']
            }
        
        return result
    
    def apply_evolution(self, evolution_result: Dict, auto_apply: bool = False) -> bool:
        """
        Apply evolution recommendations to system configuration.
        
        Args:
            evolution_result: Output from analyze_performance()
            auto_apply: If True, apply automatically (use with caution)
        
        Returns:
            True if changes were applied
        """
        from src.signals import HARD_BLOCKED_COMBOS
        
        changes = evolution_result.get('changes', {})
        blocks = evolution_result.get('blocks', [])
        unblocks = evolution_result.get('unblocks', [])
        
        if not changes and not blocks and not unblocks:
            logger.info("[Evolution] No changes recommended")
            return False
        
        # Log all recommendations
        logger.info("[Evolution] ===== THRESHOLD EVOLUTION =====")
        logger.info(f"[Evolution] Stats: {evolution_result.get('stats', {})}")
        
        for key, value in changes.items():
            rationale = evolution_result.get('rationale', {}).get(key, '')
            logger.info(f"[Evolution] {key}: {value} — {rationale}")
        
        for block in blocks:
            logger.warning(f"[Evolution] BLOCK: {block['regime']}+{block['signal']} — {block['reason']}")
        
        for unblock in unblocks:
            logger.info(f"[Evolution] UNBLOCK: {unblock['regime']}+{unblock['signal']} — {unblock['reason']}")
        
        if not auto_apply:
            logger.info("[Evolution] Set auto_apply=True to apply these changes")
            return False
        
        # Apply blocks
        for block in blocks:
            combo_key = (block['regime'], block['signal'])
            HARD_BLOCKED_COMBOS[combo_key] = {
                'wr': block['wr'],
                'n': block['n'],
                'reason': block['reason'],
                'auto_blocked': True,
                'blocked_at': datetime.now().isoformat()
            }
            logger.warning(f"[Evolution] Applied block: {combo_key}")
        
        # Apply unblocks
        for unblock in unblocks:
            combo_key = (unblock['regime'], unblock['signal'])
            if combo_key in HARD_BLOCKED_COMBOS:
                del HARD_BLOCKED_COMBOS[combo_key]
                logger.info(f"[Evolution] Applied unblock: {combo_key}")
        
        # Record evolution
        self.evolution_history.append({
            'timestamp': datetime.now().isoformat(),
            'changes': changes,
            'blocks': [b['regime'] + '+' + b['signal'] for b in blocks],
            'unblocks': [u['regime'] + '+' + u['signal'] for u in unblocks],
            'stats': evolution_result.get('stats', {})
        })
        
        logger.info("[Evolution] Changes applied successfully")
        return True
    
    def load_from_db(self) -> List[PerformanceRecord]:
        """Load closed trades from database."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            c.execute("""
                SELECT symbol, regime, signal, result, pnl_pct, confidence,
                       composite_score, entry_price, exit_price,
                       (julianday('now') - julianday(timestamp)) * 24 as held_hours,
                       timestamp
                FROM signals
                WHERE result IN ('WIN', 'LOSS')
                AND timestamp > datetime('now', '-30 days')
                ORDER BY timestamp DESC
            """)
            
            rows = c.fetchall()
            conn.close()
            
            records = []
            for row in rows:
                records.append(PerformanceRecord(
                    symbol=row['symbol'],
                    regime=row['regime'],
                    signal=row['signal'],
                    result=row['result'],
                    pnl_pct=row['pnl_pct'] or 0,
                    confidence=row['confidence'] or 50,
                    composite_score=row['composite_score'] or 50,
                    entry_price=row['entry_price'] or 0,
                    exit_price=row['exit_price'] or 0,
                    held_hours=row['held_hours'] or 0,
                    close_reason='unknown',
                    timestamp=row['timestamp']
                ))
            
            return records
            
        except Exception as e:
            logger.error(f"[Evolution] Failed to load from DB: {e}")
            return []
    
    def run_evolution_cycle(self, auto_apply: bool = False) -> Dict:
        """Complete evolution cycle: load data → analyze → apply."""
        performance_data = self.load_from_db()
        
        if len(performance_data) < self.MIN_SAMPLES:
            return {
                'applied': False,
                'reason': f'Insufficient data: {len(performance_data)}/{self.MIN_SAMPLES}'
            }
        
        evolution_result = self.analyze_performance(performance_data)
        applied = self.apply_evolution(evolution_result, auto_apply)
        
        return {
            'applied': applied,
            'recommendations': evolution_result,
            'history_count': len(self.evolution_history)
        }
    
    # Helper methods untuk get current values
    def _get_current_min_confidence(self) -> int:
        """Get current minimum confidence threshold."""
        try:
            from src.config_loader import get_config
            cfg = get_config()
            return cfg.get('signal', {}).get('long_min_score', 52)
        except:
            return 52
    
    def _get_combo_min_conf(self, regime: str, signal: str) -> int:
        """Get minimum confidence for specific regime+signal combo."""
        # Default berdasarkan RISKY_COMBOS
        from src.signals import RISKY_COMBOS
        combo = RISKY_COMBOS.get((regime, signal), {})
        return combo.get('min_conf', 50)
    
    def _get_score_threshold(self, direction: str) -> int:
        """Get score threshold for direction."""
        try:
            from src.config_loader import get_config
            cfg = get_config()
            if direction == 'LONG':
                return cfg.get('signal', {}).get('long_min_score', 52)
            else:
                return cfg.get('signal', {}).get('short_min_score', 48)
        except:
            return 52 if direction == 'LONG' else 48


# Singleton instance
_evolution_engine: Optional[ThresholdEvolution] = None


def get_evolution_engine(db_path: str = "data/screener.db") -> ThresholdEvolution:
    global _evolution_engine
    if _evolution_engine is None:
        _evolution_engine = ThresholdEvolution(db_path)
    return _evolution_engine
