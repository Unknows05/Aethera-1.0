"""
Vault Indexer — SQLite FTS5 full-text search + graph links (wikilinks).
Indexes all Markdown files in the vault for fast LLM retrieval.
"""
import os
import re
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VaultIndexer:
    """Manages SQLite FTS5 index for the knowledge vault."""

    def __init__(self, vault_dir: str = "vault", db_path: str = "vault/index.db"):
        self.vault_dir = vault_dir
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(vault_dir, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        c = conn.cursor()

        # Documents table
        c.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                title TEXT,
                folder TEXT,
                content TEXT,
                tags TEXT,
                frontmatter TEXT,
                created_at TEXT,
                updated_at TEXT,
                file_hash TEXT
            )
        """)

        # FTS5 virtual table
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(
                path, title, folder, content, tags,
                tokenize='porter unicode61'
            )
        """)

        # Graph links table (wikilinks)
        c.execute("""
            CREATE TABLE IF NOT EXISTS vault_links (
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                link_type TEXT DEFAULT 'wiki',
                context TEXT,
                PRIMARY KEY (source, target)
            )
        """)

        # Sync state table
        c.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                path TEXT PRIMARY KEY,
                file_hash TEXT,
                last_indexed TEXT
            )
        """)

        conn.commit()
        conn.close()
        logger.info(f"[VaultIndexer] Initialized at {self.db_path}")

    def index_all(self) -> int:
        """Index all Markdown files in the vault. Returns count indexed."""
        count = 0
        for root, dirs, files in os.walk(self.vault_dir):
            # Skip hidden dirs and index.db
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for filename in files:
                if not filename.endswith('.md'):
                    continue
                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, self.vault_dir)
                if self._needs_index(rel_path, filepath):
                    self._index_file(rel_path, filepath)
                    count += 1
        logger.info(f"[VaultIndexer] Indexed {count} files")
        return count

    def index_single(self, rel_path: str, filepath: str) -> bool:
        """Index a single file. Returns True if indexed."""
        if self._needs_index(rel_path, filepath):
            self._index_file(rel_path, filepath)
            return True
        return False

    def _needs_index(self, rel_path: str, filepath: str) -> bool:
        """Check if file needs re-indexing based on hash."""
        import hashlib
        try:
            with open(filepath, 'rb') as f:
                current_hash = hashlib.md5(f.read()).hexdigest()
        except Exception:
            return False

        conn = self._get_conn()
        c = conn.cursor()
        c.execute("SELECT file_hash FROM sync_state WHERE path=?", (rel_path,))
        row = c.fetchone()
        conn.close()

        if row is None:
            return True
        return row["file_hash"] != current_hash

    def _index_file(self, rel_path: str, filepath: str):
        """Parse and index a single Markdown file."""
        import hashlib
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            logger.warning(f"[VaultIndexer] Cannot read {rel_path}: {e}")
            return

        file_hash = hashlib.md5(content.encode()).hexdigest()
        folder = os.path.dirname(rel_path)
        title = os.path.splitext(os.path.basename(rel_path))[0]

        # Parse frontmatter
        frontmatter = {}
        body = content
        fm_match = re.match(r'^---\n(.*?)\n---\n(.*)', content, re.DOTALL)
        if fm_match:
            import yaml
            try:
                frontmatter = yaml.safe_load(fm_match.group(1)) or {}
            except Exception:
                pass
            body = fm_match.group(2).strip()
            title = frontmatter.get('title', title)

        # Extract tags
        tags = frontmatter.get('tags', [])
        if isinstance(tags, list):
            tags = ' '.join(tags)
        elif isinstance(tags, str):
            tags = tags

        # Extract wikilinks
        links = re.findall(r'\[\[([^\]]+)\]\]', content)

        # Upsert document
        conn = self._get_conn()
        c = conn.cursor()
        now = datetime.now().isoformat()

        # Delete existing FTS entry
        c.execute("DELETE FROM vault_fts WHERE path=?", (rel_path,))

        # Insert/update document
        c.execute("""
            INSERT OR REPLACE INTO documents
            (path, title, folder, content, tags, frontmatter, created_at, updated_at, file_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (rel_path, title, folder, body, tags, str(frontmatter), now, now, file_hash))

        # Insert FTS entry
        c.execute("""
            INSERT INTO vault_fts (path, title, folder, content, tags)
            VALUES (?, ?, ?, ?, ?)
        """, (rel_path, title, folder, body, tags))

        # Update sync state
        c.execute("""
            INSERT OR REPLACE INTO sync_state (path, file_hash, last_indexed)
            VALUES (?, ?, ?)
        """, (rel_path, file_hash, now))

        # Update links
        c.execute("DELETE FROM vault_links WHERE source=?", (rel_path,))
        for link in links:
            link_target = link.strip()
            c.execute("""
                INSERT OR IGNORE INTO vault_links (source, target, link_type)
                VALUES (?, ?, 'wiki')
            """, (rel_path, link_target))

        conn.commit()
        conn.close()

    def search(self, query: str, limit: int = 10, folder: str = None) -> List[Dict]:
        """FTS5 search across the vault."""
        conn = self._get_conn()
        c = conn.cursor()

        if folder:
            c.execute("""
                SELECT path, title, folder,
                       snippet(vault_fts, -1, '<b>', '</b>', '...', 100) as snippet,
                       rank
                FROM vault_fts
                WHERE vault_fts MATCH ? AND folder = ?
                ORDER BY rank LIMIT ?
            """, (query, folder, limit))
        else:
            c.execute("""
                SELECT path, title, folder,
                       snippet(vault_fts, -1, '<b>', '</b>', '...', 100) as snippet,
                       rank
                FROM vault_fts
                WHERE vault_fts MATCH ?
                ORDER BY rank LIMIT ?
            """, (query, limit))

        results = [dict(r) for r in c.fetchall()]
        conn.close()
        return results

    def get_links(self, path: str, depth: int = 1) -> List[Dict]:
        """Get linked documents (outgoing + incoming)."""
        conn = self._get_conn()
        c = conn.cursor()

        # Outgoing links
        c.execute("""
            SELECT target as linked_path, 'outgoing' as direction
            FROM vault_links WHERE source = ?
        """, (path,))
        outgoing = [dict(r) for r in c.fetchall()]

        # Incoming links (backlinks)
        c.execute("""
            SELECT source as linked_path, 'incoming' as direction
            FROM vault_links WHERE target = ?
        """, (path,))
        incoming = [dict(r) for r in c.fetchall()]

        conn.close()
        return outgoing + incoming

    def get_related(self, path: str, depth: int = 2) -> List[str]:
        """Get all related documents via graph traversal."""
        visited = {path}
        queue = [(path, 0)]
        related = []

        conn = self._get_conn()
        c = conn.cursor()

        while queue:
            current, current_depth = queue.pop(0)
            if current_depth >= depth:
                continue

            c.execute("SELECT target FROM vault_links WHERE source=?", (current,))
            for row in c.fetchall():
                target = row["target"]
                if target not in visited:
                    visited.add(target)
                    related.append(target)
                    queue.append((target, current_depth + 1))

        conn.close()
        return related

    def get_stats(self) -> Dict:
        """Return vault index statistics."""
        conn = self._get_conn()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM documents")
        doc_count = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM vault_links")
        link_count = c.fetchone()[0]

        c.execute("SELECT COUNT(DISTINCT folder) FROM documents")
        folder_count = c.fetchone()[0]

        c.execute("SELECT MAX(last_indexed) FROM sync_state")
        last_indexed = c.fetchone()[0]

        conn.close()
        return {
            "documents": doc_count,
            "links": link_count,
            "folders": folder_count,
            "last_indexed": last_indexed,
        }

    def delete_document(self, rel_path: str):
        """Remove a document from the index."""
        conn = self._get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM vault_fts WHERE path=?", (rel_path,))
        c.execute("DELETE FROM documents WHERE path=?", (rel_path,))
        c.execute("DELETE FROM vault_links WHERE source=?", (rel_path,))
        c.execute("DELETE FROM sync_state WHERE path=?", (rel_path,))
        conn.commit()
        conn.close()
