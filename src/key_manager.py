"""
Key Manager — Fernet AES-128-CBC encryption for exchange API keys.
Uses cryptography.fernet (authenticated encryption) with key validation.
"""
import base64
import os
import logging
from cryptography.fernet import Fernet, InvalidToken
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
        if len(key) != 44 or not key.endswith(b'='):
            logger.warning("[KeyManager] Invalid key file, regenerating (old keys lost)")
            key = _generate_key()
        else:
            try:
                # Validate key by test-decrypting a known pattern
                f_test = Fernet(key)
                test_encrypted = f_test.encrypt(b"__KEY_VALID__")
                f_test.decrypt(test_encrypted)
            except Exception:
                logger.warning("[KeyManager] Key validation failed, regenerating (old keys lost)")
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


def is_key_valid() -> bool:
    """Test if the encryption key is valid and can decrypt data."""
    try:
        if not os.path.exists(_KEY_PATH):
            return False
        f = _get_fernet()
        test = f.encrypt(b"__KEY_VALID__")
        f.decrypt(test)
        return True
    except Exception:
        return False
