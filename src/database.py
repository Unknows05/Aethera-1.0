"""
Database — SQLite for signal tracking, daily statistics, ML features.
Single-connection architecture to prevent lock contention.
"""
import sqlite3
import logging
import asyncio
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from src.utils import calculate_win_rate, calculate_pnl_pct

logger = logging.getLogger(__name__)

# ── Global singleton for scheduler use ────────────────────────
_db_singleton: Optional["ScreenerDB"] = None

def get_db() -> Optional["ScreenerDB"]:
    return _db_singleton

class ScreenerDB:
    """SQLite database — single connection with async write lock."""

    def __init__(self, db_path: str = "data/screener.db"):
        global _db_singleton
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=30.0
        )
        self.conn.row_factory = sqlite3.Row
        self._write_lock = asyncio.Lock()
        self._read_lock = threading.Lock()
        self._init_tables()
        self._enable_wal_mode()
        self._migrate_v4()
        _db_singleton = self
        logger.info(f"[DB] Initialized at {self.db_path}")

    def _enable_wal_mode(self):
        c = self.conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")
        result = c.fetchone()
        if result and result[0] == "wal":
            logger.info("[DB] WAL mode enabled")

    def _init_tables(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                entry_price REAL,
                sl REAL,
                tp REAL,
                confidence INTEGER,
                regime TEXT,
                result TEXT DEFAULT 'OPEN',
                exit_price REAL,
                exit_timestamp TEXT,
                exit_reason TEXT,
                final_price REAL,
                scan_date TEXT,
                btc_dom_change REAL DEFAULT 0.0,
                composite_score REAL DEFAULT 0.0,
                pnl_pct REAL DEFAULT 0.0,
                ml_confidence REAL DEFAULT 0.0,
                ml_passed INTEGER DEFAULT 0,
                UNIQUE(symbol, timestamp, signal)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                scan_date TEXT PRIMARY KEY,
                total_signals INTEGER DEFAULT 0,
                long_count INTEGER DEFAULT 0,
                short_count INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                open_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                gross_profit REAL DEFAULT 0.0,
                gross_loss REAL DEFAULT 0.0,
                profit_factor REAL DEFAULT 0.0,
                wr_rolling_7d REAL DEFAULT 0,
                wr_rolling_30d REAL DEFAULT 0,
                expected_value_r REAL DEFAULT 0,
                updated_at TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS signal_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                signal_time TEXT NOT NULL,
                oi_change_1h REAL DEFAULT 0,
                oi_change_4h REAL DEFAULT 0,
                oi_change_24h REAL DEFAULT 0,
                funding_rate REAL DEFAULT 0,
                ls_ratio_retail REAL DEFAULT 0,
                ls_ratio_top_trader REAL DEFAULT 0,
                taker_buy_ratio REAL DEFAULT 0,
                rsi_1h REAL DEFAULT 50,
                rsi_4h REAL DEFAULT 50,
                macd_histogram_4h REAL DEFAULT 0,
                macd_cross INTEGER DEFAULT 0,
                ema9_vs_ema21 REAL DEFAULT 0,
                adx_4h REAL DEFAULT 25,
                bb_position REAL DEFAULT 0.5,
                atr_pct REAL DEFAULT 0,
                volume_zscore_1h REAL DEFAULT 0,
                volume_zscore_4h REAL DEFAULT 0,
                candle_aggression REAL DEFAULT 0,
                btc_trend_4h INTEGER DEFAULT 0,
                market_regime INTEGER DEFAULT 0,
                confluence_score REAL DEFAULT 0,
                setup_type INTEGER DEFAULT -1,
                hours_since_last_signal REAL DEFAULT 0,
                outcome TEXT DEFAULT NULL,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ml_model_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                model_version TEXT,
                trained_at TEXT,
                samples_used INTEGER,
                wr_out_of_sample REAL,
                threshold REAL,
                top_features TEXT,
                status TEXT,
                deployed INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ml_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                symbol TEXT,
                prob_win REAL,
                model_version TEXT,
                timestamp TEXT,
                actual_outcome TEXT DEFAULT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS funding_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                funding_rate REAL,
                timestamp INTEGER NOT NULL,
                mark_price REAL,
                UNIQUE(symbol, timestamp)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS open_interest_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                open_interest REAL,
                timestamp INTEGER NOT NULL,
                price REAL,
                UNIQUE(symbol, timestamp)
            )
        """)
        # Indexes
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals(symbol, timestamp, signal)",
            "CREATE INDEX IF NOT EXISTS idx_signals_result ON signals(result)",
            "CREATE INDEX IF NOT EXISTS idx_signals_scan_date ON signals(scan_date)",
            "CREATE INDEX IF NOT EXISTS idx_signals_id_result ON signals(id, result)",
            "CREATE INDEX IF NOT EXISTS idx_sf_signal_id ON signal_features(signal_id)",
            "CREATE INDEX IF NOT EXISTS idx_sf_outcome ON signal_features(outcome)",
            "CREATE INDEX IF NOT EXISTS idx_funding_symbol_ts ON funding_history(symbol, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_oi_symbol_ts ON open_interest_history(symbol, timestamp)",
        ]:
            c.execute(idx_sql)
        # FTS5 virtual table for full-text search on decisions
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS decision_fts USING fts5(
                decision_id, symbol, regime, signal, reason, outcome,
                content='signals', content_rowid='id'
            )
        """)
        self.conn.commit()

    def _migrate_v4(self):
        c = self.conn.cursor()
        for sql, col_name in [
            ("ALTER TABLE signals ADD COLUMN ml_confidence REAL DEFAULT 0.0", "ml_confidence"),
            ("ALTER TABLE signals ADD COLUMN ml_passed INTEGER DEFAULT 0", "ml_passed"),
            ("ALTER TABLE daily_stats ADD COLUMN wr_rolling_7d REAL DEFAULT 0", "wr_rolling_7d"),
            ("ALTER TABLE daily_stats ADD COLUMN wr_rolling_30d REAL DEFAULT 0", "wr_rolling_30d"),
            ("ALTER TABLE daily_stats ADD COLUMN expected_value_r REAL DEFAULT 0", "expected_value_r"),
        ]:
            try:
                c.execute(sql)
                logger.info(f"[DB] Migration: added {col_name}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    # ── Write helpers (all go through write lock) ──────────

    async def save_signals(self, timestamp: str, results: list[dict]):
        async with self._write_lock:
            c = self.conn.cursor()
            scan_date = self.scan_date_from_ts(timestamp)
            count = 0
            signals_to_insert = []
            for r in results:
                sig = r.get("signal", "WAIT")
                if sig not in ("LONG", "SHORT"):
                    continue
                symbol = r.get("symbol")
                c.execute(
                    "SELECT id FROM signals WHERE symbol=? AND signal=? AND result='OPEN' LIMIT 1",
                    (symbol, sig),
                )
                if c.fetchone():
                    continue
                signals_to_insert.append((
                    timestamp, symbol, sig, r.get("entry"), r.get("sl"),
                    r.get("tp"), r.get("confidence", 0), r.get("regime", ""), scan_date,
                    r.get("btc_dom_change", 0.0), r.get("composite_score"),
                    r.get("ml_confidence", 0.0), 1 if r.get("ml_passed") else 0,
                ))
            if signals_to_insert:
                c.executemany(
                    """INSERT OR IGNORE INTO signals
                       (timestamp, symbol, signal, entry_price, sl, tp, confidence, regime,
                        scan_date, btc_dom_change, composite_score, ml_confidence, ml_passed)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    signals_to_insert,
                )
                count = c.rowcount
                self.conn.commit()
            if count:
                self._recalc_daily_stats(scan_date)
                logger.info(f"[DB] Saved {count} new signals")

    async def save_signal_features(self, signal_id: int, features: dict):
        async with self._write_lock:
            try:
                c = self.conn.cursor()
                cols = [
                    "signal_id", "symbol", "signal_time",
                    "oi_change_1h", "oi_change_4h", "oi_change_24h",
                    "funding_rate", "ls_ratio_retail", "ls_ratio_top_trader", "taker_buy_ratio",
                    "rsi_1h", "rsi_4h", "macd_histogram_4h", "macd_cross",
                    "ema9_vs_ema21", "adx_4h", "bb_position", "atr_pct",
                    "volume_zscore_1h", "volume_zscore_4h", "candle_aggression",
                    "btc_trend_4h", "market_regime", "confluence_score",
                    "setup_type", "hours_since_last_signal",
                ]
                vals = []
                for col in cols:
                    if col == "signal_id":
                        vals.append(signal_id)
                    elif col == "symbol":
                        vals.append(features.get("symbol", ""))
                    elif col == "signal_time":
                        vals.append(features.get("signal_time", datetime.now().isoformat()))
                    else:
                        vals.append(features.get(col, 0))
                placeholders = ", ".join(["?"] * len(cols))
                c.execute(
                    f"INSERT OR REPLACE INTO signal_features ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
                self.conn.commit()
            except Exception as e:
                logger.error(f"[DB] save_signal_features error: {e}")

    async def update_signal_features_outcome(self, signal_id: int, outcome: str):
        async with self._write_lock:
            try:
                c = self.conn.cursor()
                c.execute("UPDATE signal_features SET outcome=? WHERE signal_id=?", (outcome, signal_id))
                self.conn.commit()
            except Exception as e:
                logger.error(f"[DB] update_signal_features_outcome error: {e}")

    async def save_ml_model_meta(self, meta: dict):
        async with self._write_lock:
            try:
                c = self.conn.cursor()
                c.execute(
                    """INSERT OR REPLACE INTO ml_model_meta
                       (id, model_version, trained_at, samples_used, wr_out_of_sample,
                        threshold, top_features, status, deployed)
                       VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        meta.get("version", ""),
                        meta.get("trained_at", datetime.now().isoformat()),
                        meta.get("samples", 0),
                        meta.get("wr_out_of_sample", 0),
                        meta.get("threshold", 0.60),
                        meta.get("top_features", ""),
                        meta.get("status", "untrained"),
                        1 if meta.get("deployed") else 0,
                    ),
                )
                self.conn.commit()
            except Exception as e:
                logger.error(f"[DB] save_ml_model_meta error: {e}")

    async def save_funding_rate(self, symbol: str, funding_rate: float,
                                timestamp: int, mark_price: float = None):
        """Save funding rate via write lock (scheduler-safe)."""
        async with self._write_lock:
            try:
                self.conn.execute(
                    "INSERT OR REPLACE INTO funding_history (symbol, funding_rate, timestamp, mark_price) VALUES (?,?,?,?)",
                    (symbol, funding_rate, timestamp, mark_price),
                )
                self.conn.commit()
            except Exception as e:
                logger.error(f"[DB] save_funding_rate error: {e}")

    async def save_open_interest(self, symbol: str, open_interest: float,
                                  timestamp: int, price: float = None):
        """Save OI data point via write lock (scheduler-safe)."""
        async with self._write_lock:
            try:
                self.conn.execute(
                    "INSERT OR REPLACE INTO open_interest_history (symbol, open_interest, timestamp, price) VALUES (?,?,?,?)",
                    (symbol, open_interest, timestamp, price),
                )
                self.conn.commit()
            except Exception as e:
                logger.error(f"[DB] save_open_interest error: {e}")

    async def check_outcomes(self, prices: dict[str, float]):
        async with self._write_lock:
            c = self.conn.cursor()
            c.execute("""
                SELECT id, timestamp, symbol, signal, entry_price, sl, tp,
                       confidence, regime, result, exit_price, exit_timestamp,
                       exit_reason, final_price, scan_date, composite_score
                FROM signals WHERE result IN ('OPEN', 'PENDING')
            """)
            open_signals = c.fetchall()
            updated = 0
            now = datetime.now()
            dates_to_recalc = set()
            updates = []
            for row in open_signals:
                sid = row["id"]
                sym = row["symbol"]
                cur_price = prices.get(sym)
                if cur_price is None or cur_price <= 0:
                    continue
                signal_type = row["signal"]
                entry = row["entry_price"]
                sl = row["sl"]
                tp = row["tp"]
                ts_str = row["timestamp"]
                if sl is None or tp is None or entry is None or entry <= 0:
                    continue
                is_timeout = False
                try:
                    sig_time = datetime.fromisoformat(ts_str)
                    hours_open = (now - sig_time).total_seconds() / 3600
                    is_timeout = hours_open >= 24.0
                except ValueError:
                    pass
                result = self._check_signal_outcome(signal_type, cur_price, sl, tp)
                if result or is_timeout:
                    if not result and is_timeout:
                        is_profit = (
                            (signal_type == "LONG" and cur_price > entry)
                            or (signal_type == "SHORT" and cur_price < entry)
                        )
                        result = "WIN" if is_profit else "LOSS"
                        reason = "TIME_EXIT (PROFIT)" if is_profit else "TIME_EXIT (LOSS)"
                    else:
                        reason = "STOP LOSS HIT" if result == "LOSS" else "TAKE PROFIT HIT"
                    pnl_pct = 0.0
                    if entry > 0:
                        if signal_type == "LONG":
                            pnl_pct = (cur_price - entry) / entry * 100
                        elif signal_type == "SHORT":
                            pnl_pct = (entry - cur_price) / entry * 100
                    exit_ts = now.isoformat()
                    updates.append((result, cur_price, exit_ts, reason, cur_price, pnl_pct, sid))
                    dates_to_recalc.add(row["scan_date"])
                    updated += 1
                    logger.info(
                        f"[DB] Signal #{sid} {sym} {signal_type} -> {result} at {cur_price:.4f} ({reason}) PNL: {pnl_pct:.2f}%"
                    )
            if updates:
                c.executemany(
                    """UPDATE signals SET result=?, exit_price=?, exit_timestamp=?,
                       exit_reason=?, final_price=?, pnl_pct=? WHERE id=?""",
                    updates,
                )
                self.conn.commit()
                for d in dates_to_recalc:
                    self._recalc_daily_stats(d)
                logger.info(f"[DB] Updated {updated} signal outcomes")
            return updated

    def _check_signal_outcome(self, signal_type: str, cur_price: float,
                               sl: float, tp: float) -> str | None:
        if signal_type == "LONG":
            if cur_price <= sl: return "LOSS"
            elif cur_price >= tp: return "WIN"
        elif signal_type == "SHORT":
            if cur_price >= sl: return "LOSS"
            elif cur_price <= tp: return "WIN"
        return None

    def _recalc_daily_stats(self, date: str = None):
        c = self.conn.cursor()
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        c.execute(
            """SELECT COUNT(*) as total, SUM(signal='LONG') as longs,
               SUM(signal='SHORT') as shorts, SUM(result='WIN') as wins,
               SUM(result='LOSS') as losses, SUM(result='OPEN') as opens,
               SUM(CASE WHEN pnl_pct>0 THEN pnl_pct ELSE 0 END) as gross_profit,
               SUM(CASE WHEN pnl_pct<0 THEN ABS(pnl_pct) ELSE 0 END) as gross_loss
               FROM signals WHERE scan_date=?""",
            (date,),
        )
        row = c.fetchone()
        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        opens = row["opens"] or 0
        wr = calculate_win_rate(wins, losses)
        gp = row["gross_profit"] or 0.0
        gl = row["gross_loss"] or 0.0
        pf = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)
        c.execute(
            """INSERT OR REPLACE INTO daily_stats
               (scan_date, total_signals, long_count, short_count, wins, losses,
                open_count, win_rate, gross_profit, gross_loss, profit_factor, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (date, total, row["longs"] or 0, row["shorts"] or 0, wins, losses, opens,
             wr, gp, gl, pf, datetime.now().isoformat()),
        )
        self.conn.commit()

    # ── Read helpers ───────────────────────────────────────

    def get_summary(self) -> dict:
        c = self.conn.cursor()
        c.execute(
            """SELECT SUM(result='WIN') as wins, SUM(result='LOSS') as losses,
               SUM(result='OPEN') as opens,
               SUM(CASE WHEN pnl_pct>0 THEN pnl_pct ELSE 0 END) as gross_profit,
               SUM(CASE WHEN pnl_pct<0 THEN ABS(pnl_pct) ELSE 0 END) as gross_loss
               FROM signals"""
        )
        row = c.fetchone()
        wins = row["wins"] or 0
        losses = row["losses"] or 0
        wr = calculate_win_rate(wins, losses)
        gp = row["gross_profit"] or 0.0
        gl = row["gross_loss"] or 0.0
        pf = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)
        return {"wins": wins, "losses": losses, "open": row["opens"] or 0, "win_rate": wr, "profit_factor": pf}

    def scan_date_from_ts(self, timestamp: str) -> str:
        return datetime.fromisoformat(timestamp).strftime("%Y-%m-%d")

    def get_latest_signal_id(self) -> int:
        try:
            c = self.conn.cursor()
            c.execute("SELECT MAX(id) FROM signals")
            row = c.fetchone()
            return row[0] if row and row[0] else 0
        except Exception:
            return 0

    def get_open_signal_symbols(self) -> list[str]:
        try:
            c = self.conn.cursor()
            c.execute("SELECT DISTINCT symbol FROM signals WHERE result IN ('OPEN','PENDING')")
            return [row["symbol"] for row in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_open_signal_symbols error: {e}")
            return []

    def get_signals_with_outcomes(self, limit: int = 500, result_filter: str = None,
                                   days: int = None) -> list[dict]:
        try:
            c = self.conn.cursor()
            query = """SELECT id, timestamp, symbol, signal, entry_price, sl, tp,
                       confidence, regime, result, exit_price, exit_timestamp,
                       exit_reason, final_price, scan_date, composite_score
                       FROM signals WHERE signal IN ('LONG','SHORT')"""
            params = []
            if days:
                query += " AND timestamp >= datetime('now', ?)"
                params.append(f"-{days} days")
            if result_filter == 'closed':
                query += " AND result IN ('WIN','LOSS')"
            elif result_filter == 'open':
                query += " AND result IN ('OPEN','PENDING')"
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            c.execute(query, params)
            rows = c.fetchall()
            signals = []
            for row in rows:
                sig = dict(row)
                entry = sig.get('entry_price', 0)
                exit_p = sig.get('exit_price') or sig.get('final_price')
                result = sig.get('result', 'OPEN')
                if result in ('WIN', 'LOSS') and entry and exit_p:
                    sig['pnl_pct'] = round(calculate_pnl_pct(entry, exit_p, sig.get('signal', '')), 2)
                else:
                    sig['pnl_pct'] = 0.0
                signals.append(sig)
            return signals
        except Exception as e:
            logger.error(f"[DB] get_signals_with_outcomes error: {e}")
            return []

    def get_daily_performance(self, days: int = 7) -> list[dict]:
        try:
            c = self.conn.cursor()
            c.execute(
                """SELECT scan_date, COUNT(*) as total,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
                   SUM(CASE WHEN result IN ('OPEN','PENDING') THEN 1 ELSE 0 END) as opens,
                   SUM(CASE WHEN pnl_pct>0 THEN pnl_pct ELSE 0 END) as gross_profit,
                   SUM(CASE WHEN pnl_pct<0 THEN ABS(pnl_pct) ELSE 0 END) as gross_loss,
                   SUM(CASE WHEN signal='LONG' THEN 1 ELSE 0 END) as longs,
                   SUM(CASE WHEN signal='SHORT' THEN 1 ELSE 0 END) as shorts
                   FROM signals WHERE scan_date >= date('now', ?)
                   GROUP BY scan_date ORDER BY scan_date DESC""",
                (f"-{days} days",),
            )
            rows = c.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                wins = d.get('wins', 0) or 0
                losses = d.get('losses', 0) or 0
                total_closed = wins + losses
                d['win_rate'] = round(wins / total_closed * 100, 1) if total_closed > 0 else 0
                gp = d.get('gross_profit') or 0.0
                gl = d.get('gross_loss') or 0.0
                d['profit_factor'] = round(gp / gl, 2) if gl > 0 else (999.0 if gp > 0 else 0.0)
                result.append(d)
            return result
        except Exception as e:
            logger.error(f"[DB] get_daily_performance error: {e}")
            return []

    def get_calendar_month(self, year: int, month: int) -> list[dict]:
        try:
            c = self.conn.cursor()
            month_prefix = f"{year}-{month:02d}"
            c.execute(
                "SELECT * FROM daily_stats WHERE scan_date LIKE ? ORDER BY scan_date",
                (f"{month_prefix}%",),
            )
            return [dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_calendar_month error: {e}")
            return []

    def get_ml_model_meta(self) -> dict:
        try:
            c = self.conn.cursor()
            c.execute("SELECT * FROM ml_model_meta WHERE id=1")
            row = c.fetchone()
            return dict(row) if row else {}
        except Exception as e:
            logger.error(f"[DB] get_ml_model_meta error: {e}")
            return {}

    def get_training_data(self, lookback_days: int = 60) -> list[dict]:
        try:
            c = self.conn.cursor()
            since = (datetime.now() - timedelta(days=lookback_days)).isoformat()
            c.execute(
                """SELECT sf.*, s.result as signal_result
                   FROM signal_features sf
                   JOIN signals s ON sf.signal_id = s.id
                   WHERE sf.signal_time >= ? AND sf.outcome IS NOT NULL
                   ORDER BY sf.signal_time ASC""",
                (since,),
            )
            return [dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_training_data error: {e}")
            return []

    def save_scan_features(self, symbol: str, features: dict):
        try:
            c = self.conn.cursor()
            cols = ', '.join(features.keys())
            placeholders = ', '.join(['?'] * len(features))
            c.execute(
                f"INSERT INTO signal_features (symbol, {cols}) VALUES (?, {placeholders})",
                [symbol] + [features.get(k, 0) for k in features],
            )
            self.conn.commit()
            return c.lastrowid
        except Exception as e:
            logger.debug(f"[DB] save_scan_features error: {e}")
            return 0

    def save_ml_shadow(self, symbol: str, signal_id: int, prob_win: float, version: str):
        try:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO ml_shadow_log (signal_id, symbol, prob_win, model_version, timestamp) VALUES (?,?,?,?,?)",
                (signal_id, symbol, round(float(prob_win), 4), version, datetime.now().isoformat()),
            )
            self.conn.commit()
        except Exception as e:
            logger.debug(f"[DB] save_ml_shadow error: {e}")

    def get_ml_shadow_count(self) -> int:
        try:
            c = self.conn.cursor()
            return c.execute("SELECT COUNT(*) FROM ml_shadow_log").fetchone()[0]
        except Exception:
            return 0

    def _calculate_daily_wr_simple(self, target_date: Optional[date] = None) -> dict:
        if target_date is None:
            target_date = date.today()
        try:
            c = self.conn.cursor()
            date_str = target_date.isoformat()
            date_7d_ago = (target_date - timedelta(days=7)).isoformat()
            date_30d_ago = (target_date - timedelta(days=30)).isoformat()

            def _query(since):
                c.execute(
                    "SELECT result, pnl_pct FROM signals WHERE scan_date>=? AND result IN ('WIN','LOSS')",
                    (since,),
                )
                return c.fetchall()

            def _calc(rows):
                if not rows: return {"wr": None, "trades": 0, "pf": None, "ev_r": None}
                wins = sum(1 for r in rows if r["result"] == "WIN")
                total = len(rows)
                wr = wins / total if total > 0 else 0
                profits = [r["pnl_pct"] for r in rows if r["pnl_pct"] and r["pnl_pct"] > 0]
                losses_vals = [abs(r["pnl_pct"]) for r in rows if r["pnl_pct"] and r["pnl_pct"] < 0]
                gp = sum(profits) if profits else 0
                gl = sum(losses_vals) if losses_vals else 0
                pf = gp / gl if gl > 0 else 999.0
                ev_r = 0
                if wins > 0 and losses_vals:
                    avg_win = gp / wins
                    avg_loss = gl / len(losses_vals)
                    ev_r = wr * avg_win - (1 - wr) * avg_loss
                return {"wr": wr, "trades": total, "pf": round(pf, 2), "ev_r": round(ev_r, 4)}

            return {
                "daily": _calc(_query(date_str)),
                "rolling_7d": _calc(_query(date_7d_ago)),
                "rolling_30d": _calc(_query(date_30d_ago)),
            }
        except Exception as e:
            logger.error(f"[DB] _calculate_daily_wr_simple error: {e}")
            return {}

    def get_funding_history(self, symbol: str, limit: int = 500) -> list[dict]:
        try:
            c = self.conn.cursor()
            c.execute(
                "SELECT id, symbol, funding_rate, timestamp, mark_price FROM funding_history WHERE symbol=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit),
            )
            return [dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_funding_history error: {e}")
            return []

    def get_open_interest_history(self, symbol: str, limit: int = 500) -> list[dict]:
        try:
            c = self.conn.cursor()
            c.execute(
                "SELECT id, symbol, open_interest, timestamp, price FROM open_interest_history WHERE symbol=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit),
            )
            return [dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] get_open_interest_history error: {e}")
            return []

    async def clear_all_data(self):
        async with self._write_lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM daily_stats")
            self.conn.commit()
        logger.info("[DB] All data cleared")

    # ── FTS5 decision search ─────────────────────────────

    def search_decisions(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search across indexed decisions using FTS5."""
        try:
            # Sanitize FTS5 query: escape special FTS5 characters
            sanitized = query.replace('"', '""').replace('*', '').replace('-', ' ')
            c = self.conn.cursor()
            c.execute(
                """SELECT dfts.decision_id, dfts.symbol, dfts.regime,
                   dfts.signal, dfts.reason, dfts.outcome,
                   s.entry_price, s.exit_price, s.pnl_pct, s.timestamp, s.confidence
                   FROM decision_fts dfts
                   JOIN signals s ON dfts.decision_id = s.id
                   WHERE decision_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (sanitized, limit),
            )
            return [dict(r) for r in c.fetchall()]
        except Exception as e:
            logger.error(f"[DB] search_decisions error: {e}")
            return []

    async def index_decision(self, decision_id: int, symbol: str, regime: str,
                             signal: str, reason: str = "", outcome: str = ""):
        """Insert a decision into the FTS5 index via write lock."""
        async with self._write_lock:
            try:
                c = self.conn.cursor()
                c.execute(
                    """INSERT INTO decision_fts
                       (decision_id, symbol, regime, signal, reason, outcome)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (decision_id, symbol, regime or "", signal or "",
                     reason or "", outcome or ""),
                )
                self.conn.commit()
            except Exception as e:
                logger.error(f"[DB] index_decision error: {e}")

    def close(self):
        self.conn.close()
