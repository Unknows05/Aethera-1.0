"""
Auth module — Token-based user authentication with secure password hashing.
Uses bcrypt for password hashing and HMAC-SHA256 with time-limited tokens.
File-persisted secret so tokens survive server restarts.
"""
import sqlite3
import hashlib
import hmac
import secrets
import logging
import time
import os
from datetime import datetime
from typing import Dict, Optional

try:
    import bcrypt
    _HAS_BCRYPT = True
except ImportError:
    _HAS_BCRYPT = False

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_HOURS = 24

_JWT_SECRET_PATH = "data/.jwt_secret"


def _get_jwt_secret() -> str:
    if os.path.exists(_JWT_SECRET_PATH):
        with open(_JWT_SECRET_PATH) as f:
            return f.read().strip()
    secret = secrets.token_hex(32)
    os.makedirs("data", exist_ok=True)
    with open(_JWT_SECRET_PATH, "w") as f:
        f.write(secret)
    os.chmod(_JWT_SECRET_PATH, 0o600)
    return secret


def _hash_password(password: str) -> str:
    """Secure password hashing with bcrypt (falls back to PBKDF2-like if bcrypt unavailable)."""
    if _HAS_BCRYPT:
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode(), salt).decode()
    # Fallback: PBKDF2-like with SHA256 iterated 10000x
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    for _ in range(9999):
        h = hashlib.sha256(f"{salt}:{h}".encode()).hexdigest()
    return f"pbkdf2${salt}${h}"


def _verify_password(password: str, stored: str) -> bool:
    if stored.startswith("pbkdf2$"):
        # Legacy PBKDF2-like hash
        try:
            _, salt, h = stored.split("$", 2)
            computed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
            for _ in range(9999):
                computed = hashlib.sha256(f"{salt}:{computed}".encode()).hexdigest()
            return hmac.compare_digest(computed, h)
        except Exception:
            return False
    if _HAS_BCRYPT:
        try:
            return bcrypt.checkpw(password.encode(), stored.encode())
        except Exception:
            return False
    return False


def _create_token(user_id: int, username: str) -> str:
    now = int(time.time())
    exp = now + TOKEN_EXPIRY_HOURS * 3600
    payload = f"{user_id}:{username}:{exp}"
    sig = hmac.new(
        _get_jwt_secret().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{sig}"


def _verify_token(token: str) -> Optional[Dict]:
    try:
        parts = token.split(":")
        if len(parts) != 4:
            return None
        user_id, username, exp_str, sig = parts
        if int(time.time()) > int(exp_str):
            return None
        payload = f"{user_id}:{username}:{exp_str}"
        expected = hmac.new(
            _get_jwt_secret().encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return {"user_id": int(user_id), "username": username}
    except Exception:
        return None


class AuthManager:
    def __init__(self, db_path: str = "data/screener.db"):
        self.db_path = db_path
        self._init_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT,
                paper_mode INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                exchange TEXT DEFAULT 'binance',
                api_key_encrypted TEXT NOT NULL,
                secret_encrypted TEXT NOT NULL,
                testnet INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                balance REAL DEFAULT 100.0,
                peak_balance REAL DEFAULT 100.0,
                drawdown_pct REAL DEFAULT 0.0,
                tier TEXT DEFAULT 'aggressive',
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS auto_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                signal_id INTEGER,
                symbol TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity REAL NOT NULL,
                pnl_usd REAL,
                pnl_pct REAL,
                exit_reason TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                max_position_pct REAL DEFAULT 5.0,
                max_leverage INTEGER DEFAULT 5,
                risk_per_trade_pct REAL DEFAULT 2.0,
                target_capital REAL DEFAULT 10000.0,
                stop_trading_at_dd REAL DEFAULT 20.0,
                auto_trade_enabled INTEGER DEFAULT 0,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.commit()
        conn.close()

    def register(self, username: str, password: str, email: str = "") -> Dict:
        if len(username) < 3 or len(password) < 6:
            return {"ok": False, "error": "Username min 3 chars, password min 6 chars"}
        conn = self._get_conn()
        try:
            h = _hash_password(password)
            now = datetime.now().isoformat()
            c = conn.cursor()
            c.execute(
                "INSERT INTO users (username, password_hash, email, paper_mode, created_at) VALUES (?,?,?,?,?)",
                (username, h, email, 1, now))
            user_id = c.lastrowid
            c.execute("INSERT INTO user_settings (user_id, updated_at) VALUES (?,?)", (user_id, now))
            c.execute(
                "INSERT INTO user_equity (user_id, balance, peak_balance, drawdown_pct, tier, updated_at) VALUES (?,?,?,?,?,?)",
                (user_id, 100.0, 100.0, 0.0, "aggressive", now))
            conn.commit()
            token = _create_token(user_id, username)
            return {"ok": True, "token": token, "user_id": user_id, "username": username}
        except sqlite3.IntegrityError:
            return {"ok": False, "error": "Username already taken"}
        except Exception as e:
            logger.error(f"Register error: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def login(self, username: str, password: str) -> Dict:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT id, username, password_hash FROM users WHERE username=?", (username,))
            row = c.fetchone()
            if not row or not _verify_password(password, row["password_hash"]):
                return {"ok": False, "error": "Invalid username or password"}
            token = _create_token(row["id"], row["username"])
            return {"ok": True, "token": token, "user_id": row["id"], "username": row["username"]}
        except Exception as e:
            logger.error(f"Login error: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def verify(self, token: str) -> Optional[Dict]:
        return _verify_token(token)

    def get_user(self, user_id: int) -> Optional[Dict]:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT id, username, email, paper_mode, created_at FROM users WHERE id=?", (user_id,))
            row = c.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_settings(self, user_id: int) -> Dict:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))
            row = c.fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def update_settings(self, user_id: int, settings: Dict) -> Dict:
        conn = self._get_conn()
        try:
            allowed = ["max_position_pct", "max_leverage", "risk_per_trade_pct",
                       "target_capital", "stop_trading_at_dd", "auto_trade_enabled"]
            updates = {k: settings[k] for k in allowed if k in settings}
            if not updates: return {"ok": False, "error": "No valid settings"}
            updates["updated_at"] = datetime.now().isoformat()
            c = conn.cursor()
            set_clause = ", ".join(f"{k}=?" for k in updates)
            c.execute(f"UPDATE user_settings SET {set_clause} WHERE user_id=?", list(updates.values()) + [user_id])
            conn.commit()
            return {"ok": True, "settings": self.get_settings(user_id)}
        except Exception as e:
            logger.error(f"Update settings error: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def get_equity(self, user_id: int) -> Dict:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM user_equity WHERE user_id=?", (user_id,))
            row = c.fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()

    def update_equity(self, user_id: int, balance: float, peak: float = None):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT peak_balance FROM user_equity WHERE user_id=?", (user_id,))
            row = c.fetchone()
            if peak is None:
                peak = max(row["peak_balance"], balance) if row else balance
            dd = (peak - balance) / peak * 100 if peak > 0 else 0
            tier = "aggressive"
            progress = balance / _user_target(user_id, conn)
            if progress >= 0.80: tier = "ultra_conservative"
            elif progress >= 0.50: tier = "conservative"
            elif progress >= 0.10: tier = "balanced"
            c.execute(
                "INSERT OR REPLACE INTO user_equity (user_id, balance, peak_balance, drawdown_pct, tier, updated_at) VALUES (?,?,?,?,?,?)",
                (user_id, balance, peak, round(dd, 2), tier, datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logger.error(f"Update equity error: {e}")
        finally:
            conn.close()

    def save_api_key(self, user_id: int, api_key: str, secret: str,
                     exchange: str = "binance", testnet: bool = False) -> Dict:
        conn = self._get_conn()
        try:
            from src.key_manager import encrypt_key
            c = conn.cursor()
            c.execute(
                "INSERT INTO user_api_keys (user_id, exchange, api_key_encrypted, secret_encrypted, testnet, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, exchange, encrypt_key(api_key), encrypt_key(secret),
                 1 if testnet else 0, datetime.now().isoformat()))
            conn.commit()
            return {"ok": True, "id": c.lastrowid}
        except Exception as e:
            logger.error(f"Save API key error: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def get_api_keys(self, user_id: int) -> list:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT id, exchange, testnet, created_at FROM user_api_keys WHERE user_id=?", (user_id,))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()

    def delete_api_key(self, user_id: int, key_id: int) -> bool:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("DELETE FROM user_api_keys WHERE id=? AND user_id=?", (key_id, user_id))
            conn.commit()
            return c.rowcount > 0
        finally:
            conn.close()

    def save_trade(self, user_id: int, signal_id: int, symbol: str,
                   entry_price: float, quantity: float) -> int:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO auto_trades (user_id, signal_id, symbol, entry_price, quantity, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, signal_id, symbol, entry_price, quantity, datetime.now().isoformat()))
            conn.commit()
            return c.lastrowid
        finally:
            conn.close()

    def close_trade(self, trade_id: int, exit_price: float, pnl_usd: float,
                    pnl_pct: float, exit_reason: str):
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute(
                "UPDATE auto_trades SET exit_price=?, pnl_usd=?, pnl_pct=?, exit_reason=?, closed_at=? WHERE id=?",
                (exit_price, pnl_usd, pnl_pct, exit_reason, datetime.now().isoformat(), trade_id))
            conn.commit()
        finally:
            conn.close()

    def get_trade_history(self, user_id: int, limit: int = 50) -> list:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM auto_trades WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()

    def get_open_trades(self, user_id: int) -> list:
        conn = self._get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM auto_trades WHERE user_id=? AND exit_price IS NULL", (user_id,))
            return [dict(r) for r in c.fetchall()]
        finally:
            conn.close()


def _user_target(user_id: int, conn) -> float:
    c = conn.cursor()
    c.execute("SELECT target_capital FROM user_settings WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row["target_capital"] if row else 10000.0


_auth_instance: Optional[AuthManager] = None


def get_auth(db_path: str = "data/screener.db") -> AuthManager:
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = AuthManager(db_path)
    return _auth_instance
