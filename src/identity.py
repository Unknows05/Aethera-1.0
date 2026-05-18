"""
Agent Identity — Ed25519 keypair management for swarm authentication.

Security note: The Ed25519 seed is stored in plaintext on disk
(data/identity.ed25519). For production deployments, replace this with
OS keyring storage (e.g. keyring library, systemd credentials, or
hardware-backed keystore). The key file is chmod 600 but the seed is
not encrypted at rest.
"""
import json
import os
import logging
import hashlib
from typing import Optional

from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder

logger = logging.getLogger(__name__)

IDENTITY_PATH = "data/identity.ed25519"


class AgentIdentity:
    def __init__(self, key_path: str = IDENTITY_PATH):
        self._key_path = key_path
        self._signing_key: Optional[SigningKey] = None
        self._verify_key: Optional[VerifyKey] = None
        self._public_key_hex: str = ""

    @classmethod
    def generate(cls, key_path: str = IDENTITY_PATH) -> "AgentIdentity":
        sk = SigningKey.generate()
        vk = sk.verify_key
        seed_hex = sk.encode(encoder=HexEncoder).decode()
        pubkey_hex = vk.encode(encoder=HexEncoder).decode()

        identity = cls(key_path)
        identity._signing_key = sk
        identity._verify_key = vk
        identity._public_key_hex = pubkey_hex

        data = {
            "seed": seed_hex,
            "public_key": pubkey_hex,
            "agent_id": identity.agent_id,
        }
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        with open(key_path, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(key_path, 0o600)
        logger.info(f"[Identity] Generated new keypair: {identity.agent_id}")
        return identity

    @classmethod
    def load(cls, key_path: str = IDENTITY_PATH) -> Optional["AgentIdentity"]:
        if not os.path.exists(key_path):
            return None
        try:
            with open(key_path) as f:
                data = json.load(f)
            seed_hex = data["seed"]
            sk = SigningKey(seed_hex, encoder=HexEncoder)
            vk = sk.verify_key
            pubkey_hex = vk.encode(encoder=HexEncoder).decode()

            identity = cls(key_path)
            identity._signing_key = sk
            identity._verify_key = vk
            identity._public_key_hex = pubkey_hex
            return identity
        except Exception as e:
            logger.error(f"[Identity] Failed to load keypair: {e}")
            return None

    @property
    def agent_id(self) -> str:
        if not self._public_key_hex:
            return ""
        return self._public_key_hex[:16]

    @property
    def public_key_hex(self) -> str:
        return self._public_key_hex

    def sign(self, payload: dict) -> str:
        message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signed = self._signing_key.sign(message)
        sig_bytes = signed.signature
        return sig_bytes.hex()

    @staticmethod
    def verify(pubkey_hex: str, payload: dict, signature_hex: str) -> bool:
        try:
            vk = VerifyKey(pubkey_hex, encoder=HexEncoder)
            message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            sig_bytes = bytes.fromhex(signature_hex)
            vk.verify(message, sig_bytes)
            return True
        except Exception:
            return False

    def is_loaded(self) -> bool:
        return self._signing_key is not None


_identity: Optional[AgentIdentity] = None


def get_identity() -> AgentIdentity:
    global _identity
    if _identity is None:
        _identity = AgentIdentity.load()
        if _identity is None:
            _identity = AgentIdentity.generate()
    return _identity
