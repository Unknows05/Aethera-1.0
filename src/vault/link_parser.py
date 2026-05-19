"""
Link Parser — parses wikilinks [[like_this]] from Markdown files.
Extracts and stores graph links for vault traversal.
"""
import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LinkParser:
    """Parses and manages wikilinks in vault Markdown files."""

    # Wikilink pattern: [[target]] or [[target|display text]]
    WIKILINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')

    @staticmethod
    def extract_links(content: str) -> List[Dict]:
        """Extract all wikilinks from content. Returns list of {target, display}."""
        links = []
        for match in LinkParser.WIKILINK_PATTERN.finditer(content):
            target = match.group(1).strip()
            display = match.group(2) or target
            links.append({"target": target, "display": display})
        return links

    @staticmethod
    def extract_links_from_file(filepath: str) -> List[Dict]:
        """Extract wikilinks from a file."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            return LinkParser.extract_links(content)
        except Exception as e:
            logger.warning(f"[LinkParser] Cannot read {filepath}: {e}")
            return []

    @staticmethod
    def find_backlinks(content: str, target: str) -> List[str]:
        """Find all references to a target in content."""
        pattern = re.compile(r'\[\[' + re.escape(target) + r'(?:\|[^\]]+)?\]\]')
        return [match.group(0) for match in pattern.finditer(content)]

    @staticmethod
    def sanitize_link_name(name: str) -> str:
        """Convert a link name to a safe filename."""
        return re.sub(r'[^a-z0-9_-]', '_', name.lower().strip())

    @staticmethod
    def format_links_as_prompt(links: List[Dict]) -> str:
        """Format links for LLM prompt injection."""
        if not links:
            return ""
        lines = ["## Related Knowledge"]
        for link in links:
            lines.append(f"- [[{link['target']}]] ({link['display']})")
        return "\n".join(lines)
