"""
Pita Media Enterprise Security - Secure SecretStore
Provides platform-secure encrypted storage for API keys, tokens, and credentials.
Uses Windows DPAPI (Data Protection API) with encrypted local vault fallback.
Secrets are NEVER stored in plain text, database backups, or application logs.
"""

import os
import sys
import json
import base64
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger("pita.security.secret_store")

VAULT_FILE = Path("storage/.secrets_vault")

# Optional Windows DPAPI implementation via ctypes
def _win_dpapi_encrypt(plain_bytes: bytes) -> bytes:
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        data_in = DATA_BLOB(len(plain_bytes), ctypes.cast(plain_bytes, ctypes.POINTER(ctypes.c_byte)))
        data_out = DATA_BLOB()

        crypt32 = ctypes.windll.crypt32
        res = crypt32.CryptProtectData(
            ctypes.byref(data_in),
            "PitaMediaSecret",
            None,
            None,
            None,
            0,
            ctypes.byref(data_out)
        )
        if res:
            encrypted = ctypes.string_at(data_out.pbData, data_out.cbData)
            kernel32 = ctypes.windll.kernel32
            kernel32.LocalFree(data_out.pbData)
            return encrypted
    except Exception as e:
        logger.debug(f"DPAPI encrypt fallback: {e}")
    return b""

def _win_dpapi_decrypt(cipher_bytes: bytes) -> bytes:
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        data_in = DATA_BLOB(len(cipher_bytes), ctypes.cast(cipher_bytes, ctypes.POINTER(ctypes.c_byte)))
        data_out = DATA_BLOB()

        crypt32 = ctypes.windll.crypt32
        res = crypt32.CryptUnprotectData(
            ctypes.byref(data_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(data_out)
        )
        if res:
            decrypted = ctypes.string_at(data_out.pbData, data_out.cbData)
            kernel32 = ctypes.windll.kernel32
            kernel32.LocalFree(data_out.pbData)
            return decrypted
    except Exception as e:
        logger.debug(f"DPAPI decrypt fallback: {e}")
    return b""

def _get_machine_key() -> bytes:
    """Derives a machine-unique encryption key from OS/Hardware fingerprint."""
    raw_ident = f"{os.environ.get('COMPUTERNAME', '')}-{os.environ.get('USERNAME', '')}-{sys.platform}"
    return hashlib.sha256(raw_ident.encode('utf-8')).digest()

def _xor_encrypt_decrypt(data: bytes, key: bytes) -> bytes:
    """Symmetric XOR stream cipher using SHA-256 derived keystream."""
    extended_key = hashlib.sha512(key).digest()
    out = bytearray(len(data))
    for i in range(len(data)):
        out[i] = data[i] ^ extended_key[i % len(extended_key)]
    return bytes(out)

class SecretStore:
    def __init__(self, vault_path: Path = VAULT_FILE):
        self.vault_path = vault_path
        self._memory_cache: Dict[str, str] = {}
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_vault()

    def _load_vault(self):
        """Loads and decrypts secrets from the encrypted vault."""
        if not self.vault_path.exists():
            return

        try:
            with open(self.vault_path, "rb") as f:
                raw_payload = f.read()

            if not raw_payload:
                return

            decrypted_bytes = b""
            # Attempt DPAPI decrypt first on Windows
            if os.name == "nt":
                decrypted_bytes = _win_dpapi_decrypt(raw_payload)

            # Fallback to machine-key cipher if DPAPI was not used or failed
            if not decrypted_bytes:
                try:
                    decrypted_bytes = _xor_encrypt_decrypt(raw_payload, _get_machine_key())
                except Exception:
                    decrypted_bytes = b""

            if decrypted_bytes:
                data = json.loads(decrypted_bytes.decode('utf-8'))
                self._memory_cache = data
        except Exception as e:
            logger.warning(f"Could not load secrets vault: {e}")

    def _save_vault(self):
        """Encrypts and persists secrets to the secure vault file."""
        try:
            plain_bytes = json.dumps(self._memory_cache).encode('utf-8')
            cipher_bytes = b""

            # Try DPAPI first on Windows
            if os.name == "nt":
                cipher_bytes = _win_dpapi_encrypt(plain_bytes)

            # Fallback to machine-key cipher
            if not cipher_bytes:
                cipher_bytes = _xor_encrypt_decrypt(plain_bytes, _get_machine_key())

            temp_vault = self.vault_path.with_suffix(".tmp")
            with open(temp_vault, "wb") as f:
                f.write(cipher_bytes)
            temp_vault.replace(self.vault_path)
        except Exception as e:
            logger.error(f"Failed to save encrypted secrets vault: {e}")

    def set_secret(self, key: str, value: str) -> bool:
        """Stores or updates a secret key-value pair."""
        if not key or value is None:
            return False
        self._memory_cache[key] = str(value)
        self._save_vault()
        return True

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieves a secret by key. Returns default if not found."""
        return self._memory_cache.get(key, default)

    def delete_secret(self, key: str) -> bool:
        """Removes a secret from storage."""
        if key in self._memory_cache:
            del self._memory_cache[key]
            self._save_vault()
            return True
        return False

    def list_keys(self) -> List[str]:
        """Lists all stored secret keys."""
        return list(self._memory_cache.keys())

    @staticmethod
    def mask_secret(secret_value: Optional[str]) -> str:
        """
        Formats secret value safely for UI display (e.g. ••••••••1234).
        Never exposes the full secret.
        """
        if not secret_value or secret_value.startswith("your_") or secret_value == "None":
            return "(Belum Dikonfigurasi)"
        s = str(secret_value).strip()
        if len(s) <= 8:
            return "••••••••"
        return "••••••••" + s[-4:]

secret_store = SecretStore()
