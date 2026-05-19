"""
Vault Search — FTS5 search tool for LLM retrieval.
Provides search interface for screening/management agents to query the vault.
"""
import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class VaultSearch:
    """Search interface for the knowledge vault."""

    def __init__(self, indexer=None):
        self.indexer = indexer

    def search(self, query: str, limit: int = 5,
               folder: Optional[str] = None) -> List[Dict]:
        """FTS5 search across the vault."""
        if not self.indexer:
            return []

        # Escape special FTS5 characters
        safe_query = re.sub(r'[^\w\s\-\+\.]', '', query)
        if not safe_query:
            return []

        results = self.indexer.search(safe_query, limit=limit, folder=folder)
        return results

    def search_skills(self, query: str, limit: int = 5) -> List[Dict]:
        """Search only in skills folder."""
        return self.search(query, limit=limit, folder="skills")

    def search_lessons(self, query: str, limit: int = 5) -> List[Dict]:
        """Search only in lessons folder."""
        return self.search(query, limit=limit, folder="lessons")

    def search_by_regime(self, regime: str, limit: int = 5) -> List[Dict]:
        """Search by regime keyword."""
        return self.search(f"regime:{regime}", limit=limit)

    def get_related(self, path: str, depth: int = 2) -> List[str]:
        """Get related documents via graph traversal."""
        if not self.indexer:
            return []
        return self.indexer.get_related(path, depth=depth)

    def format_for_llm(self, query: str, limit: int = 5) -> str:
        """Format search results for LLM prompt injection."""
        results = self.search(query, limit=limit)
        if not results:
            return ""

        lines = ["## Vault Knowledge"]
        for r in results:
            title = r.get("title", r.get("path", "Unknown"))
            snippet = r.get("snippet", "")
            # Strip HTML tags from snippet
            snippet = re.sub(r'<[^>]+>', '', snippet)
            lines.append(f"### {title}")
            lines.append(snippet)
            lines.append("")

        return "\n".join(lines)

    def get_vault_summary(self) -> str:
        """Get a summary of vault contents for LLM context."""
        if not self.indexer:
            return "Vault not initialized."

        stats = self.indexer.get_stats()
        return (f"Vault contains {stats['documents']} documents, "
                f"{stats['links']} links, across {stats['folders']} folders. "
                f"Last indexed: {stats['last_indexed'] or 'never'}.")
