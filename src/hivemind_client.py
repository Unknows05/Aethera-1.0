"""
HiveMind Client — swarm intelligence sync with PnL anonymization + SSRF prevention.
Pull crowd lessons, push anonymized local lessons (signed with Ed25519).
"""
import json
import os
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from src.identity import AgentIdentity, get_identity

logger = logging.getLogger(__name__)

HIVEMIND_CACHE = "data/hivemind_cache.json"
_HIVEMIND_URL_ENV = os.getenv("HIVEMIND_URL", "")

# Default HiveMind server — like Meridian defaults to api.agentmeridian.xyz
# Users auto-connect to swarm on startup. Override via .env HIVEMIND_URL="" to disable.
_DEFAULT_HIVEMIND_URL = "https://hivemind.aethera-s1.com"

# SSRF: block internal, private, and link-local IPs including IPv6
_BLOCKED_HOSTS = re.compile(
    r"(localhost|127\.|0\.0\.0\.0|"
    r"10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|169\.254\.|"
    r"::1|\[::\]|\[::ffff:|\[fe80:|\[fc00:|\[fd00:|\[::1\])",
    re.IGNORECASE,
)

# PnL sanitization patterns: strip USD amounts, percentages, entry/exit prices
_PNL_SANITIZE = re.compile(
    r'\$[\d,]+(?:\.\d+)?'
    r'|[+-]?\d+\.?\d*\s*%'
    r'|(?:entry|exit|price|stop|target)\s*(?:at|=|:)\s*[\d,.]+',
    re.IGNORECASE,
)


def _anonymize_text(text: str) -> str:
    """Remove PnL amounts, prices, and percentages from text before sharing."""
    if not text:
        return text
    return _PNL_SANITIZE.sub("[redacted]", text)


def _anonymize_lesson(lesson: dict) -> dict:
    """Return a copy of the lesson with PnL data removed."""
    clean = dict(lesson)
    clean.pop("pnl_pct", None)
    clean.pop("pnl_usd", None)
    clean.pop("entry_price", None)
    clean.pop("exit_price", None)
    # Sanitize text fields
    for key in ("rule", "exit_reason", "reasoning"):
        if key in clean and isinstance(clean[key], str):
            clean[key] = _anonymize_text(clean[key])
    return clean


def _validate_hivemind_url(url: str) -> str:
    """Validate HiveMind URL — block internal/private addresses (SSRF prevention)."""
    if not url:
        return ""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        logger.warning(f"[HiveMind] Invalid URL scheme: {url[:50]}")
        return ""
    host = url.split("://")[1].split("/")[0].split(":")[0]
    if _BLOCKED_HOSTS.search(host):
        logger.warning(f"[HiveMind] Blocked internal URL: {host}")
        return ""
    return url


# Resolve URL: .env override → default server → disabled
if _HIVEMIND_URL_ENV.strip():
    HIVEMIND_URL = _validate_hivemind_url(_HIVEMIND_URL_ENV)
else:
    HIVEMIND_URL = _validate_hivemind_url(_DEFAULT_HIVEMIND_URL)


class HiveMindClient:
    def __init__(self, agent_id: str = None, server_url: str = None):
        self.agent_id = agent_id or os.getenv("HIVEMIND_AGENT_ID", "")
        self.server_url = server_url or HIVEMIND_URL
        self.enabled = bool(self.server_url)

        identity = get_identity()
        if identity.is_loaded() and not self.agent_id:
            self.agent_id = identity.agent_id

        if not self.agent_id:
            self.agent_id = f"agt_{self._generate_id()}"

        if self.enabled:
            logger.info(f"[HiveMind] Connected to {self.server_url} (agent={self.agent_id})")
            # Auto-register on startup (non-blocking)
            try:
                self.register()
            except Exception as e:
                logger.debug(f"[HiveMind] Auto-register deferred: {e}")
        else:
            logger.info("[HiveMind] No server URL — swarm disabled.")

    def _generate_id(self) -> str:
        import secrets
        return secrets.token_hex(8)

    def _sign_body(self, body: dict) -> dict:
        identity = get_identity()
        if identity.is_loaded():
            body["pubkey_hex"] = identity.public_key_hex
            sig = identity.sign(body)
            body["_signature"] = sig
        return body

    def _request(self, path: str, method: str = "GET", body: dict = None) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            import requests
            url = f"{self.server_url.rstrip('/')}/api/hivemind/{path}"
            headers = {"Content-Type": "application/json"}
            if method == "GET":
                r = requests.get(url, headers=headers, timeout=10)
            else:
                r = requests.post(url, headers=headers, json=body, timeout=10)
            if r.status_code in (200, 201):
                return r.json()
            else:
                logger.debug(f"[HiveMind] Request {path} returned {r.status_code}")
        except Exception as e:
            logger.debug(f"[HiveMind] Request failed: {e}")
        return None

    def is_enabled(self) -> bool:
        return self.enabled

    def register(self) -> bool:
        identity = get_identity()
        if not identity.is_loaded():
            logger.warning("[HiveMind] No identity loaded, cannot register")
            return False
        body = {
            "agent_id": self.agent_id,
            "version": "1.6.0",
            "pubkey_hex": identity.public_key_hex,
        }
        body = self._sign_body(body)
        resp = self._request("agents/register", "POST", body)
        return resp is not None and resp.get("ok", False)

    def push_lesson(self, rule: str, tags: list = None, regime: str = "",
                    signal: str = "", result: str = "", pnl_pct: float = 0,
                    confidence: float = 0, held_hours: float = 0,
                    exit_reason: str = "") -> bool:
        """Push anonymized lesson — NO PnL amounts, NO entry/exit prices."""
        lesson = {
            "rule": _anonymize_text(rule[:400]),
            "tags": (tags or [])[:5],
            "regime": regime,
            "signal": signal,
            "result": result,
            "confidence": confidence,
            "held_hours": round(held_hours, 2),
            "exit_reason": _anonymize_text(exit_reason[:200]),
            "source": "autonomous",
        }
        body = {
            "agent_id": self.agent_id,
            "timestamp": datetime.now().isoformat(),
            "lesson": _anonymize_lesson(lesson),
        }
        body = self._sign_body(body)
        resp = self._request("lessons/push", "POST", body)
        return resp is not None

    def push_event(self, symbol: str, regime: str, signal: str, result: str,
                   pnl_pct: float, held_hours: float, exit_reason: str,
                   confidence: float = 0) -> bool:
        """Push anonymized event — NO PnL data in the payload."""
        event = {
            "symbol": symbol,
            "regime": regime,
            "signal": signal,
            "result": result,
            "held_hours": round(held_hours, 2),
            "exit_reason": _anonymize_text(exit_reason[:200]),
            "confidence": confidence,
        }
        body = {
            "agent_id": self.agent_id,
            "timestamp": datetime.now().isoformat(),
            "event": _anonymize_lesson(event),
        }
        body = self._sign_body(body)
        resp = self._request("events/push", "POST", body)
        return resp is not None

    def push_performance_event(self, symbol: str, regime: str, signal: str,
                               result: str, pnl_pct: float, held_hours: float,
                               exit_reason: str, confidence: float = 0) -> bool:
        """Push anonymized performance event."""
        return self.push_event(symbol, regime, signal, result, pnl_pct, held_hours, exit_reason, confidence)

    def pull_lessons(self, regime: str = None, signal: str = None, limit: int = 20) -> List[Dict]:
        cache = self._load_cache()
        lessons = cache.get("shared_lessons", [])
        if regime and signal:
            lessons = [l for l in lessons if l.get("regime") == regime and l.get("signal") == signal]
        params = f"lessons/pull?limit={limit}"
        if regime:
            params += f"&regime={regime}"
        if signal:
            params += f"&signal={signal}"
        resp = self._request(params)
        if resp and resp.get("ok"):
            cache["shared_lessons"] = resp.get("lessons", [])
            cache["pulled_at"] = datetime.now().isoformat()
            self._save_cache(cache)
            lessons = cache["shared_lessons"]
        return lessons[:limit]

    def pull_thresholds(self) -> Dict:
        resp = self._request("thresholds")
        if resp and resp.get("ok"):
            return resp.get("thresholds", {})
        return {}

    def get_swarm_status(self) -> dict:
        resp = self._request("stats")
        if resp and resp.get("ok"):
            return resp.get("stats", {})
        return {}

    def push_lesson_anonymized(self, symbol: str, regime: str, signal: str,
                               result: str, confidence: int,
                               held_hours: float = 0, exit_reason: str = "") -> bool:
        """Push lesson WITHOUT any PnL amounts — direction only (WIN/LOSS)."""
        return self.push_lesson(
            rule=f"{symbol} {signal} {result} in {regime} regime",
            tags=["auto", "anonymized"],
            regime=regime,
            signal=signal,
            result=result,
            pnl_pct=0,
            confidence=confidence,
            held_hours=held_hours,
            exit_reason=exit_reason,
        )

    def push_debate_outcome(self, symbol: str, regime: str, signal: str,
                            bull_score: float, bear_score: float,
                            final_decision: str, overrode: bool = False,
                            reasoning: str = "") -> bool:
        """Push anonymized debate outcome — reasoning is PnL-stripped."""
        debate = {
            "symbol": symbol,
            "regime": regime,
            "signal": signal,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "final_decision": final_decision,
            "overrode": overrode,
            "reasoning": _anonymize_text(reasoning[:300]),
        }
        body = {
            "agent_id": self.agent_id,
            "timestamp": datetime.now().isoformat(),
            "debate": debate,
        }
        body = self._sign_body(body)
        resp = self._request("debate/push", "POST", body)
        return resp is not None

    def push_skill(self, name: str, description: str, procedure: str,
                   pitfalls: str, evidence: str, regime: str = "",
                   signal: str = "", trade_count: int = 0,
                   win_rate: float = 0.0) -> bool:
        """Push a proven skill (evidence-based, not raw vault files)."""
        skill = {
            "name": name[:100],
            "description": _anonymize_text(description[:300]),
            "procedure": _anonymize_text(procedure[:500]),
            "pitfalls": _anonymize_text(pitfalls[:300]),
            "evidence": _anonymize_text(evidence[:300]),
            "regime": regime,
            "signal": signal,
            "trade_count": trade_count,
            "win_rate": win_rate,
        }
        body = {
            "agent_id": self.agent_id,
            "timestamp": datetime.now().isoformat(),
            "skill": skill,
        }
        body = self._sign_body(body)
        resp = self._request("skills/push", "POST", body)
        return resp is not None

    def pull_skills(self, regime: str = "", signal: str = "",
                    min_trades: int = 3, limit: int = 20) -> list:
        """Pull proven skills from swarm."""
        params = f"?min_trades={min_trades}&limit={limit}"
        if regime:
            params += f"&regime={regime}"
        if signal:
            params += f"&signal={signal}"
        resp = self._request(f"skills/pull{params}")
        if resp and resp.get("ok"):
            return resp.get("skills", [])
        return []

    def _load_cache(self) -> dict:
        if os.path.exists(HIVEMIND_CACHE):
            try:
                with open(HIVEMIND_CACHE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"shared_lessons": [], "pulled_at": None, "agent_id": self.agent_id}

    def _save_cache(self, data: dict):
        os.makedirs("data", exist_ok=True)
        with open(HIVEMIND_CACHE, "w") as f:
            json.dump(data, f, indent=2)


_hive: Optional[HiveMindClient] = None


def get_hivemind() -> HiveMindClient:
    global _hive
    if _hive is None:
        _hive = HiveMindClient()
    return _hive




