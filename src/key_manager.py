"""
Key Manager — Fernet AES-256 symmetric encryption for exchange API keys.
Uses cryptography.fernet (industry standard) instead of XOR obfuscation.
"""
import base64
import os
import logging
from cryptography.fernet import Fernet
from typing import Optional

logger = logging.getLogger(__name__)

_KEY_PATH = "data/.encryption_key"
_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            key = f.read()
        # Validate: Fernet key = 32 url-safe base64 bytes = 44 chars
        if len(key) != 44 or not key.endswith(b'='):
            logger.warning("[KeyManager] Invalid key file, regenerating")
            key = _generate_key()
    else:
        key = _generate_key()

    _fernet = Fernet(key)
    return _fernet


def _generate_key() -> bytes:
    key = Fernet.generate_key()
    os.makedirs("data", exist_ok=True)
    with open(_KEY_PATH, "wb") as f:
        f.write(key)
    os.chmod(_KEY_PATH, 0o600)
    logger.info("[KeyManager] Generated new Fernet key")
    return key


def encrypt_key(plaintext: str) -> str:
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_key(ciphertext: str) -> str:
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
