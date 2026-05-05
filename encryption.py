"""
encryption.py - AES-256 (Fernet) encryption for API keys stored in SQLite.

Generate a master key once and store it in your .env:
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Set ENCRYPTION_KEY=<output> in your .env file.
WARNING: If you lose this key, all stored API keys become unreadable.
         Back it up securely (password manager, hardware token, etc).
"""

import os
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_KEY = os.getenv("ENCRYPTION_KEY", "").encode()
_cipher: Fernet | None = None


def _get_cipher() -> Fernet:
    global _cipher, _KEY
    if _cipher:
        return _cipher
    if not _KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            "  python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
            "Then add ENCRYPTION_KEY=<value> to your .env file."
        )
    _cipher = Fernet(_KEY)
    return _cipher


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return a base64-encoded ciphertext string."""
    if not plaintext:
        return ""
    try:
        return _get_cipher().encrypt(plaintext.encode()).decode()
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise


def decrypt(ciphertext: str) -> str:
    """Decrypt a ciphertext string back to plaintext."""
    if not ciphertext:
        return ""
    try:
        return _get_cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("Decryption failed — wrong key or corrupted data.")
        return ""
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return ""


def is_configured() -> bool:
    """Return True if the encryption key is set and valid."""
    try:
        _get_cipher()
        return True
    except RuntimeError:
        return False
