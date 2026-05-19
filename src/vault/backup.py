"""
Vault Backup — backup and restore vault to/from tar.gz files.
"""
import os
import tarfile
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class VaultBackup:
    """Manages vault backup and restore operations."""

    def __init__(self, vault_dir: str = "vault", backup_dir: str = "data/vault-backups"):
        self.vault_dir = vault_dir
        self.backup_dir = backup_dir
        os.makedirs(backup_dir, exist_ok=True)

    def backup(self) -> Optional[str]:
        """Create a backup of the vault. Returns backup filename."""
        date_str = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        filename = f"vault-backup-{date_str}.tar.gz"
        filepath = os.path.join(self.backup_dir, filename)

        try:
            with tarfile.open(filepath, "w:gz") as tar:
                tar.add(self.vault_dir, arcname="vault")
            logger.info(f"[VaultBackup] Backup created: {filename}")
            return filename
        except Exception as e:
            logger.error(f"[VaultBackup] Backup failed: {e}")
            return None

    def restore(self, backup_file: str) -> bool:
        """Restore vault from a backup file. Returns True on success."""
        # Resolve path
        if not os.path.isabs(backup_file):
            backup_file = os.path.join(self.backup_dir, backup_file)

        if not os.path.exists(backup_file):
            logger.error(f"[VaultBackup] Backup file not found: {backup_file}")
            return False

        try:
            with tarfile.open(backup_file, "r:gz") as tar:
                # Extract to parent of vault_dir
                parent = os.path.dirname(self.vault_dir)
                tar.extractall(path=parent)
            logger.info(f"[VaultBackup] Restored from: {backup_file}")
            return True
        except Exception as e:
            logger.error(f"[VaultBackup] Restore failed: {e}")
            return False

    def list_backups(self) -> List[Dict]:
        """List all available backups."""
        backups = []
        if not os.path.isdir(self.backup_dir):
            return backups

        for filename in sorted(os.listdir(self.backup_dir)):
            if not filename.endswith(".tar.gz"):
                continue
            filepath = os.path.join(self.backup_dir, filename)
            stat = os.stat(filepath)
            backups.append({
                "filename": filename,
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })

        return backups

    def cleanup(self, keep: int = 5) -> int:
        """Remove old backups, keeping only the most recent N. Returns count removed."""
        backups = self.list_backups()
        if len(backups) <= keep:
            return 0

        to_remove = backups[:-keep]
        removed = 0
        for backup in to_remove:
            filepath = os.path.join(self.backup_dir, backup["filename"])
            try:
                os.remove(filepath)
                removed += 1
            except Exception as e:
                logger.warning(f"[VaultBackup] Cannot remove {backup['filename']}: {e}")

        if removed > 0:
            logger.info(f"[VaultBackup] Cleaned up {removed} old backups (kept {keep})")
        return removed

    def get_latest_backup(self) -> Optional[Dict]:
        """Get the most recent backup."""
        backups = self.list_backups()
        return backups[-1] if backups else None
