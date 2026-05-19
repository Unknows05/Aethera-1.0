"""
Aethera Knowledge Vault — Obsidian-style Markdown vault with FTS5 search and graph links.
"""
from src.vault.indexer import VaultIndexer
from src.vault.skill_manager import SkillManager
from src.vault.lesson_manager import LessonManager
from src.vault.memory import VaultMemory
from src.vault.search import VaultSearch
from src.vault.link_parser import LinkParser
from src.vault.backup import VaultBackup

__all__ = [
    "VaultIndexer",
    "SkillManager",
    "LessonManager",
    "VaultMemory",
    "VaultSearch",
    "LinkParser",
    "VaultBackup",
]
