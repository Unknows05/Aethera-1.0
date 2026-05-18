"""
Audit Chain — tamper-evident HMAC decision log.
Each entry links to the previous via SHA-256, forming an immutable chain.
"""
import json
import os
import hashlib
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

AUDIT_PATH = "data/audit_chain.json"
GENESIS_HASH = "0" * 64


class AuditChain:
    def __init__(self, path: str = AUDIT_PATH):
        self._path = path
        self._entries: list = []
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    data = json.load(f)
                self._entries = data.get("chain", [])
            except Exception as e:
                logger.error(f"[Audit] Failed to load chain: {e}")
                self._entries = []

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump({"chain": self._entries}, f, indent=2)

    def _hash_entry(self, entry: dict, prev_hash: str) -> str:
        payload = json.dumps({"entry": entry, "prev": prev_hash}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def append(self, entry: dict) -> str:
        prev_hash = self.get_last_hash()
        new_hash = self._hash_entry(entry, prev_hash)
        record = {
            "index": len(self._entries),
            "timestamp": datetime.now().isoformat(),
            "hash": new_hash,
            "prev": prev_hash,
            "entry": entry,
        }
        self._entries.append(record)
        self._save()
        return new_hash

    def verify(self) -> bool:
        if not self._entries:
            return True
        for i, record in enumerate(self._entries):
            expected_prev = self._entries[i - 1]["hash"] if i > 0 else GENESIS_HASH
            if record.get("prev") != expected_prev:
                logger.warning(f"[Audit] Chain broken at index {i}: prev mismatch")
                return False
            expected_hash = self._hash_entry(record.get("entry", {}), expected_prev)
            if record.get("hash") != expected_hash:
                logger.warning(f"[Audit] Chain broken at index {i}: hash mismatch")
                return False
        return True

    def get_last_hash(self) -> str:
        if self._entries:
            return self._entries[-1]["hash"]
        return GENESIS_HASH

    def get_entries(self, limit: int = 100) -> list:
        return self._entries[-limit:]

    def clear(self):
        self._entries = []
        self._save()


_chain: Optional[AuditChain] = None


def get_audit_chain() -> AuditChain:
    global _chain
    if _chain is None:
        _chain = AuditChain()
    return _chain
