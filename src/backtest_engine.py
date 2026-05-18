"""
Backtest Engine — Recalculate PnL & feed ML training pipeline.

Primary purpose: Fix 5,963 signals with zero/missing PnL by recalculating
from actual SL/TP distances. This provides proper training labels for ML.

Secondary: Run strategy backtest to validate EV per signal combo.
"""
import sqlite3
import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class BacktestResult:
    trades: int
    wins: int
    losses: int
    initial_capital: float
    final_capital: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    expected_value_r: float
    win_rate: float
    avg_rr_ratio: float
    regime_breakdown: Dict[str, dict]
    equity_curve: List[float]


class BacktestEngine:
    def __init__(self, db_path: str = "data/screener.db"):
        self.db_path = db_path

    def recalc_and_fix_pnl(self) -> int:
        """Update pnl_pct for all signals using actual SL/TP distances.
        Returns number of rows fixed. Used as ML training data preparation."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Get all closed signals with zero/missing PnL
        rows = c.execute("""
            SELECT id, result, entry_price, sl, tp
            FROM signals WHERE result IN ('WIN','LOSS')
            AND (pnl_pct IS NULL OR pnl_pct = 0)
            AND entry_price > 0 AND sl > 0 AND tp > 0
        """).fetchall()

        fixed = 0
        for row in rows:
            sig_id, result, entry, sl, tp = row
            sl_dist = abs(entry - sl) / entry
            tp_dist = abs(tp - entry) / entry
            if sl_dist <= 0:
                continue
            # Correct PnL: risk % for loss, reward % for win
            if result == 'WIN':
                pnl = tp_dist * 100
            else:
                pnl = -sl_dist * 100
            c.execute("UPDATE signals SET pnl_pct = ? WHERE id = ?", (round(pnl, 6), sig_id))
            # Sync outcome to signal_features for ML training
            outcome = 'WIN' if pnl > 0 else 'LOSS'
            c.execute(
                "UPDATE signal_features SET outcome = ? WHERE signal_id = ? AND outcome IS NULL",
                (outcome, sig_id)
            )
            fixed += 1

        conn.commit()
        conn.close()
        return fixed

    def get_training_ready_count(self) -> int:
        """Count signal_features with proper outcomes for ML training."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        count = c.execute("""
            SELECT COUNT(*) FROM signal_features 
            WHERE outcome IS NOT NULL
        """).fetchone()[0]
        conn.close()
        return count

    def run(self, initial_capital: float = 10000.0, risk_pct: float = 0.02,
            regime_filter: str = None, direction_filter: str = None,
            start_date: str = None) -> BacktestResult:

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        where = "WHERE result IN ('WIN','LOSS')"
        params = []
        if regime_filter:
            where += " AND regime = ?"
            params.append(regime_filter)
        if direction_filter:
            where += " AND signal = ?"
            params.append(direction_filter)
        if start_date:
            where += " AND timestamp >= ?"
            params.append(start_date)

        c.execute(f"SELECT * FROM signals {where} ORDER BY timestamp ASC", params)
        rows = c.fetchall()
        conn.close()

        if not rows:
            return BacktestResult(0, 0, 0, initial_capital, initial_capital, 0, 0, 0, 0, 0, 0, 0, {}, [])

        capital = initial_capital
        equity = [capital]
        gross_win, gross_loss = 0.0, 0.0
        wins, losses = 0, 0
        regime_data = {}
        trade_returns = []

        for s in rows:
            entry = s['entry_price'] or 0
            sl = s['sl'] or 0
            tp = s['tp'] or 0

            if entry > 0 and sl > 0 and tp > 0:
                sl_dist = abs(entry - sl) / entry
                tp_dist = abs(tp - entry) / entry
                rr = tp_dist / sl_dist if sl_dist > 0 else 2.0
            else:
                rr = 2.0

            risk_usd = initial_capital * risk_pct
            if s['result'] == 'WIN':
                pnl = risk_usd * rr
                gross_win += pnl
                wins += 1
            else:
                pnl = -risk_usd
                gross_loss += abs(pnl)
                losses += 1

            capital = max(0.01, capital + pnl)
            equity.append(round(capital, 2))
            trade_returns.append(pnl / initial_capital)

            combo = f"{s['regime']}+{s['signal']}"
            regime_data.setdefault(combo, {'w': 0, 't': 0, 'pnl': 0.0})
            regime_data[combo]['t'] += 1
            regime_data[combo]['pnl'] += pnl
            if s['result'] == 'WIN':
                regime_data[combo]['w'] += 1

        total_trades = len(rows)
        wr = wins / total_trades if total_trades > 0 else 0
        pf = gross_win / gross_loss if gross_loss > 0 else float('inf')
        ev_r = round(sum(trade_returns) / total_trades * (initial_capital / (initial_capital * risk_pct)),
                     4) if total_trades > 0 else 0

        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            if v > peak: peak = v
            dd = (peak - v) / peak
            if dd > max_dd: max_dd = dd

        sharpe = 0.0
        if len(trade_returns) > 1:
            mean_r = sum(trade_returns) / len(trade_returns)
            variance = sum((r - mean_r) ** 2 for r in trade_returns) / len(trade_returns)
            std_r = math.sqrt(variance)
            if std_r > 0:
                sharpe = round((mean_r / std_r) * math.sqrt(len(trade_returns)), 3)

        avg_rr = sum(
            (abs(r['tp'] - r['entry_price']) / abs(r['sl'] - r['entry_price'])
             if r['entry_price'] and r['sl'] and r['tp'] and r['sl'] != r['entry_price']
             else 2.0)
            for r in rows
        ) / total_trades if total_trades > 0 else 2.0

        regime_bd = {}
        for combo, data in regime_data.items():
            t = data['t']
            r_wr = data['w'] / t if t > 0 else 0
            r_pnl = data['pnl']
            r_ev = round(r_wr * 2 - (1 - r_wr) * 1, 3)
            regime_bd[combo] = {
                'wr': round(r_wr * 100, 1), 'n': t,
                'pnl_total': round(r_pnl, 2),
                'pnl_per_trade': round(r_pnl / t, 2) if t > 0 else 0,
                'ev_est': r_ev,
            }

        return BacktestResult(
            trades=total_trades, wins=wins, losses=losses,
            initial_capital=initial_capital,
            final_capital=round(capital, 2),
            total_return_pct=round((capital - initial_capital) / initial_capital * 100, 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            sharpe_ratio=sharpe, profit_factor=round(pf, 2),
            expected_value_r=ev_r, win_rate=round(wr * 100, 2),
            avg_rr_ratio=round(avg_rr, 2),
            regime_breakdown=regime_bd, equity_curve=equity,
        )