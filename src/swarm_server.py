"""
HiveMind Swarm Server — central intelligence hub for VPS deployment.
Collects lessons, performance metrics, and crowd-optimized thresholds.
"""
import json
import sqlite3
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from src.identity import AgentIdentity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = "data/swarm.db"

app = FastAPI(
    title="Aethera HiveMind",
    description="Swarm intelligence server for decentralized trading agents",
    version="6.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            version TEXT DEFAULT '6.0.0',
            last_seen TEXT NOT NULL,
            pubkey_hex TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            rule TEXT NOT NULL,
            tags TEXT DEFAULT '[]',
            regime TEXT DEFAULT '',
            signal TEXT DEFAULT '',
            outcome TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            score INTEGER DEFAULT 1,
            timestamp TEXT NOT NULL,
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            regime TEXT DEFAULT '',
            signal TEXT DEFAULT '',
            result TEXT DEFAULT '',
            pnl_pct REAL DEFAULT 0.0,
            held_hours REAL DEFAULT 0.0,
            exit_reason TEXT DEFAULT '',
            timestamp TEXT NOT NULL,
            FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_lessons_regime_signal ON lessons(regime, signal)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lessons_rule ON lessons(rule)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lessons_score ON lessons(score DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_performance_agent ON performance(agent_id)")
    conn.commit()
    conn.close()


def update_crowd_scores(rule: str) -> int:
    conn = _db()
    c = conn.cursor()
    c.execute("SELECT id FROM lessons WHERE rule = ? ORDER BY id", (rule,))
    matching = [r["id"] for r in c.fetchall()]
    new_score = len(matching)
    if matching:
        placeholders = ",".join(["?"] * len(matching))
        c.execute(
            f"UPDATE lessons SET score = ? WHERE id IN ({placeholders})",
            [new_score] + matching,
        )
    conn.commit()
    conn.close()
    return new_score


def _verify_signature(agent_id: str, pubkey_hex: str, payload: dict, signature: str) -> bool:
    verify_payload = {k: v for k, v in payload.items() if k != "_signature"}
    return AgentIdentity.verify(pubkey_hex, verify_payload, signature)


@app.get("/health")
async def health():
    conn = _db()
    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT agent_id) FROM agents")
    agents_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM lessons")
    lessons_count = c.fetchone()[0]
    conn.close()
    return {
        "status": "ok",
        "service": "aethera-hivemind",
        "version": "6.0.0",
        "agents": agents_count,
        "lessons": lessons_count,
    }


@app.post("/api/hivemind/agents/register")
async def agents_register(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id", "").strip()
        pubkey_hex = data.get("pubkey_hex", "").strip()
        version = data.get("version", "6.0.0")
        if not agent_id or not pubkey_hex:
            raise HTTPException(status_code=400, detail="agent_id and pubkey_hex required")

        signature = data.pop("_signature", "")
        verify_payload = {k: v for k, v in data.items() if k != "_signature"}

        if signature and not AgentIdentity.verify(pubkey_hex, verify_payload, signature):
            raise HTTPException(status_code=403, detail="Invalid signature")

        conn = _db()
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO agents (agent_id, version, last_seen, pubkey_hex) VALUES (?, ?, ?, ?)",
            (agent_id, version, datetime.now(timezone.utc).isoformat(), pubkey_hex),
        )
        conn.commit()
        conn.close()
        logger.info(f"[HiveMind] Agent registered: {agent_id}")
        return {"ok": True, "agent_id": agent_id, "message": "registered"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[HiveMind] Register error: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/hivemind/lessons/push")
async def lessons_push(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id", "").strip()
        lesson = data.get("lesson", {})
        if not agent_id or not lesson:
            raise HTTPException(status_code=400, detail="agent_id and lesson required")

        signature = data.pop("_signature", "")
        verify_payload = {k: v for k, v in data.items() if k != "_signature"}

        conn = _db()
        c = conn.cursor()
        c.execute("SELECT pubkey_hex FROM agents WHERE agent_id = ?", (agent_id,))
        agent_row = c.fetchone()
        conn.close()

        if agent_row and agent_row["pubkey_hex"]:
            pubkey = agent_row["pubkey_hex"]
            if signature and not AgentIdentity.verify(pubkey, verify_payload, signature):
                raise HTTPException(status_code=403, detail="Invalid signature")

        rule = lesson.get("rule", "")[:400]
        tags = json.dumps(lesson.get("tags", [])[:5])
        regime = lesson.get("regime", "")
        signal = lesson.get("signal", "")
        outcome = lesson.get("result", lesson.get("outcome", ""))
        confidence = float(lesson.get("confidence", 0))
        timestamp = data.get("timestamp", datetime.now(timezone.utc).isoformat())

        conn = _db()
        c = conn.cursor()
        c.execute(
            """INSERT INTO lessons (agent_id, rule, tags, regime, signal, outcome, confidence, score, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (agent_id, rule, tags, regime, signal, outcome, confidence, timestamp),
        )
        conn.commit()
        conn.close()

        score = update_crowd_scores(rule)

        logger.info(f"[HiveMind] Lesson from {agent_id}: {regime}/{signal} score={score}")
        return {"ok": True, "crowd_score": score}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[HiveMind] Lesson push error: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/hivemind/lessons/pull")
async def lessons_pull(
    regime: str = Query(None),
    signal: str = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    try:
        conn = _db()
        c = conn.cursor()
        query = "SELECT * FROM lessons WHERE 1=1"
        params: list = []
        if regime:
            query += " AND regime = ?"
            params.append(regime)
        if signal:
            query += " AND signal = ?"
            params.append(signal)
        query += " ORDER BY score DESC LIMIT ?"
        params.append(limit)
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()

        lessons = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            lessons.append(d)

        return {"ok": True, "lessons": lessons, "count": len(lessons)}
    except Exception as e:
        logger.error(f"[HiveMind] Lesson pull error: {e}")
        return {"ok": False, "error": str(e), "lessons": []}


@app.post("/api/hivemind/performance/push")
async def performance_push(request: Request):
    try:
        data = await request.json()
        agent_id = data.get("agent_id", "").strip()
        event = data.get("event", {})

        if not agent_id or not event:
            raise HTTPException(status_code=400, detail="agent_id and event required")

        signature = data.pop("_signature", "")
        verify_payload = {k: v for k, v in data.items() if k != "_signature"}

        conn = _db()
        c = conn.cursor()
        c.execute("SELECT pubkey_hex FROM agents WHERE agent_id = ?", (agent_id,))
        agent_row = c.fetchone()

        if agent_row and agent_row["pubkey_hex"]:
            pubkey = agent_row["pubkey_hex"]
            if signature and not AgentIdentity.verify(pubkey, verify_payload, signature):
                raise HTTPException(status_code=403, detail="Invalid signature")

        symbol = event.get("symbol", "")
        regime = event.get("regime", "")
        signal = event.get("signal", "")
        result = event.get("result", "")
        pnl_pct = float(event.get("pnl_pct", 0))
        held_hours = float(event.get("held_hours", 0))
        exit_reason = event.get("exit_reason", "")[:200]
        timestamp = data.get("timestamp", datetime.now(timezone.utc).isoformat())

        c.execute(
            """INSERT INTO performance (agent_id, symbol, regime, signal, result, pnl_pct, held_hours, exit_reason, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent_id, symbol, regime, signal, result, pnl_pct, held_hours, exit_reason, timestamp),
        )
        conn.commit()
        conn.close()

        logger.info(f"[HiveMind] Performance from {agent_id}: {symbol} {result} {pnl_pct:+.2f}%")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[HiveMind] Performance push error: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/hivemind/thresholds")
async def thresholds():
    try:
        conn = _db()
        c = conn.cursor()

        c.execute("""
            SELECT regime, signal, COUNT(*) as total,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins
            FROM performance WHERE result IN ('WIN','LOSS')
            GROUP BY regime, signal HAVING total >= 5
        """)
        combos = c.fetchall()
        conn.close()

        thresholds = {"long_min_score": 56, "short_max_score": 44, "confidence_floor": 35}

        regime_danger: dict = {}
        regime_safe: dict = {}
        for row in combos:
            wr = row["wins"] / row["total"] * 100 if row["total"] > 0 else 0
            key = f"{row['regime']}+{row['signal']}"
            if wr < 35:
                regime_danger[key] = round(wr, 1)
            elif wr > 65:
                regime_safe[key] = round(wr, 1)

        thresholds["danger_combos"] = regime_danger
        thresholds["safe_combos"] = regime_safe
        thresholds["recommendation"] = "crowd-vetted from swarm data"

        return {"ok": True, "thresholds": thresholds}
    except Exception as e:
        logger.error(f"[HiveMind] Thresholds error: {e}")
        return {"ok": True, "thresholds": {
            "long_min_score": 56, "short_max_score": 44,
            "confidence_floor": 35, "recommendation": "default",
        }}


@app.get("/api/hivemind/stats")
async def swarm_stats():
    try:
        conn = _db()
        c = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT agent_id) FROM performance")
        total_agents = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM lessons")
        total_lessons = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM performance WHERE result IN ('WIN','LOSS')")
        total_trades = c.fetchone()[0]
        c.execute("SELECT SUM(pnl_pct) FROM performance WHERE result IN ('WIN','LOSS')")
        total_pnl = c.fetchone()[0] or 0.0
        conn.close()
        return {"ok": True, "stats": {
            "total_agents": total_agents,
            "total_lessons": total_lessons,
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
        }}
    except Exception as e:
        logger.error(f"[HiveMind] Stats error: {e}")
        return {"ok": False, "error": str(e)}


@app.on_event("startup")
async def startup():
    _init_db()
    logger.info("[HiveMind] Swarm server started on :8900")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8900)
