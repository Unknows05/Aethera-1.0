"""
Vault Memory — bounded persistent memory stored as Markdown in vault/memory/.
Hermes-inspired: compact entries for LLM system prompt injection.
"""
import os
import re
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MEMORY_PATH = "vault/memory/MEMORY.md"
MAX_CHARS = 2200
CONSOLIDATION_THRESHOLD = 0.80
ENTRY_SEPARATOR = "§"


class VaultMemory:
    """Bounded persistent memory stored as Markdown with §-separated entries."""

    def __init__(self, vault_dir: str = "vault", max_chars: int = MAX_CHARS):
        self.memory_path = os.path.join(vault_dir, "memory", "MEMORY.md")
        self.max_chars = max_chars
        os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)

    def _read(self) -> str:
        if os.path.exists(self.memory_path):
            with open(self.memory_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _write(self, content: str):
        with open(self.memory_path, "w", encoding="utf-8") as f:
            f.write(content)

    def _get_entries(self) -> List[str]:
        raw = self._read()
        if not raw:
            return []
        entries = [e.strip() for e in raw.split(ENTRY_SEPARATOR) if e.strip()]
        return entries

    def _build(self, entries: List[str]) -> str:
        return ENTRY_SEPARATOR.join(entries)

    def add_entry(self, text: str) -> bool:
        """Add a memory entry. Returns False if >80% full (needs consolidation)."""
        text = text.strip()
        if not text:
            return False

        entries = self._get_entries()

        # Auto-detect duplicates (exact match → reject)
        for existing in entries:
            if existing == text:
                return False

        usage = self.get_usage()
        if usage["percent_used"] > CONSOLIDATION_THRESHOLD * 100:
            return False

        entries.append(text)
        new_content = self._build(entries)
        if len(new_content) > self.max_chars:
            return False

        self._write(new_content)
        return True

    def replace_entry(self, old_text_substring: str, new_text: str) -> bool:
        """Replace entry by substring match. Only replaces the first match."""
        entries = self._get_entries()
        for i, entry in enumerate(entries):
            if old_text_substring in entry:
                entries[i] = new_text.strip()
                self._write(self._build(entries))
                return True
        return False

    def remove_entry(self, text_substring: str) -> bool:
        """Remove entry by substring match. Only removes the first match."""
        entries = self._get_entries()
        for i, entry in enumerate(entries):
            if text_substring in entry:
                entries.pop(i)
                self._write(self._build(entries))
                return True
        return False

    def consolidate(self) -> str:
        """Compact entries when >80% full. Returns the consolidated text."""
        entries = self._get_entries()
        if not entries:
            return ""

        combined = " | ".join(entries)
        if len(combined) > self.max_chars * 0.6:
            keep = entries[-5:] if len(entries) > 5 else entries
            summary = f"[Consolidated {len(entries) - len(keep)} older entries]"
            consolidated = [summary] + keep
        else:
            consolidated = [combined]

        new_content = self._build(consolidated)
        if len(new_content) > self.max_chars:
            new_content = new_content[:self.max_chars]

        self._write(new_content)
        return new_content

    def get_all(self) -> str:
        """Return all entries for system prompt injection."""
        return self._read()

    def get_usage(self) -> Dict:
        """Return memory usage stats."""
        content = self._read()
        entries = self._get_entries()
        current_chars = len(content)
        return {
            "current_chars": current_chars,
            "max_chars": self.max_chars,
            "percent_used": round(current_chars / self.max_chars * 100, 1) if self.max_chars > 0 else 0,
            "entry_count": len(entries),
        }

    def clear(self):
        """Clear all memory."""
        self._write("")
        logger.info("[VaultMemory] Memory cleared")
