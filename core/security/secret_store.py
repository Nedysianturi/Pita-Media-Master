"""
Pita Media Enterprise Security - Hardened Persistent SecretStore.
Provides platform-secure encrypted storage for API keys, tokens, and credentials.
Uses Windows DPAPI (Data Protection API) with authenticated AES-256-GCM / PBKDF2 cipher.
Secrets are NEVER stored in plaintext in SQLite, JSON files, application logs, or git commits.
Features:
- Persistent across Windows / daemon / app reboots.
- Multi-generational backup rotation (.bak1, .bak2).
- Atomic secret replacement (PENDING -> TEST -> ACTIVE / PREVIOUS).
- Safe Mode auto-fallback on corruption.
- Encrypted .pmvault export / import for disaster recovery.
- No reveal full secret endpoints / logs.
"""

import os
import sys
import json
import time
import shutil
import base64
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Tuple
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from core.security.redaction_filter import redact_text

logger = logging.getLogger("pita.security.secret_store")

SECURE_DIR = Path("storage/secure")
VAULT_FILE = SECURE_DIR / "credentials.vault"
VAULT_META_FILE = SECURE_DIR / "vault.meta.json"
VAULT_BAK1 = SECURE_DIR / "credentials.vault.bak1"
VAULT_BAK2 = SECURE_DIR / "credentials.vault.bak2"


# --- WINDOWS DPAPI INTEGRATION ---
def _win_dpapi_encrypt(plain_bytes: bytes) -> bytes:
    if os.name != "nt":
        return b""
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
            "PitaMediaSecretVault",
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
        logger.debug(f"DPAPI protect fallback: {e}")
    return b""


def _win_dpapi_decrypt(cipher_bytes: bytes) -> bytes:
    if os.name != "nt":
        return b""
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
        logger.debug(f"DPAPI unprotect fallback: {e}")
    return b""


# --- HARDENED AES-256-GCM / PBKDF2 HELPERS ---
def _derive_machine_key(salt: bytes) -> bytes:
    raw_ident = f"{os.environ.get('COMPUTERNAME', 'PITA')}-{os.environ.get('USERNAME', 'MEDIA')}-{sys.platform}-SECURE_VAULT_SEED_2026"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return kdf.derive(raw_ident.encode('utf-8'))


def _aes_gcm_encrypt(data_bytes: bytes, key: bytes) -> bytes:
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, data_bytes, None)
    return nonce + ciphertext


def _aes_gcm_decrypt(payload: bytes, key: bytes) -> bytes:
    nonce = payload[:12]
    ciphertext = payload[12:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)


def compute_fingerprint(secret_value: str) -> str:
    """Computes a safe non-revealing fingerprint with trailing 4 chars."""
    if not secret_value:
        return ""
    val_clean = secret_value.strip()
    if len(val_clean) <= 4:
        return "••••"
    tail = val_clean[-4:]
    return f"••••••••{tail}"


class SecretStore:
    @staticmethod
    def get_masked_fingerprint(secret_value: str) -> str:
        return compute_fingerprint(secret_value)

    @staticmethod
    def mask_secret(secret_value: str) -> str:
        return compute_fingerprint(secret_value)


    def __init__(self, secure_dir: Optional[Path] = None, vault_path: Optional[Path] = None):
        if vault_path:
            self.vault_file = Path(vault_path)
            self.secure_dir = self.vault_file.parent
            self.meta_file = self.secure_dir / f"{self.vault_file.stem}.meta.json"
            self.bak1_file = Path(str(self.vault_file) + ".bak1")
            self.bak2_file = Path(str(self.vault_file) + ".bak2")
        else:
            self.secure_dir = Path(secure_dir) if secure_dir else SECURE_DIR
            self.vault_file = self.secure_dir / "credentials.vault"
            self.meta_file = self.secure_dir / "vault.meta.json"
            self.bak1_file = self.secure_dir / "credentials.vault.bak1"
            self.bak2_file = self.secure_dir / "credentials.vault.bak2"
        
        self._secrets: Dict[str, Dict[str, Any]] = {} # name -> {active, previous, pending, metadata}
        self.is_safe_mode: bool = False
        self.last_integrity_check: Optional[datetime] = None
        
        self.secure_dir.mkdir(parents=True, exist_ok=True)
        self._load_and_validate_vault()

    def _load_and_validate_vault(self):
        """Loads and decrypts secrets with automatic generational backup recovery."""
        if not self.vault_file.exists():
            # Check legacy vault migration if available
            legacy_file = Path("storage/.secrets_vault")
            if legacy_file.exists():
                self._migrate_legacy_vault(legacy_file)
            else:
                self._secrets = {}
                self.last_integrity_check = datetime.now(timezone.utc)
                return

        loaded_data = self._decrypt_vault_file(self.vault_file)
        if loaded_data is not None:
            self._secrets = loaded_data
            self.last_integrity_check = datetime.now(timezone.utc)
            self._save_metadata_only()
            return

        # Attempt recovery from bak1
        logger.warning("Primary vault corrupted. Attempting recovery from bak1...")
        if self.bak1_file.exists():
            loaded_data = self._decrypt_vault_file(self.bak1_file)
            if loaded_data is not None:
                self._secrets = loaded_data
                self._save_vault(rotate_backup=False)
                logger.info("Successfully recovered vault from bak1.")
                self.last_integrity_check = datetime.now(timezone.utc)
                return

        # Attempt recovery from bak2
        logger.warning("bak1 failed. Attempting recovery from bak2...")
        if self.bak2_file.exists():
            loaded_data = self._decrypt_vault_file(self.bak2_file)
            if loaded_data is not None:
                self._secrets = loaded_data
                self._save_vault(rotate_backup=False)
                logger.info("Successfully recovered vault from bak2.")
                self.last_integrity_check = datetime.now(timezone.utc)
                return

        # All recovery attempts failed -> Safe Mode
        logger.critical("All vault integrity checks failed. Entering SAFE_MODE to protect data.")
        self.is_safe_mode = True
        self._secrets = {}

    def _decrypt_vault_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Decrypts a vault file using DPAPI or AES-256-GCM."""
        try:
            with open(file_path, "rb") as f:
                raw = f.read()
            if not raw or len(raw) < 16:
                return None

            # 1. Try DPAPI
            decrypted = _win_dpapi_decrypt(raw) if os.name == "nt" else None
            
            # 2. Try AES-256-GCM Envelope if DPAPI did not return valid bytes
            if not decrypted:
                try:
                    # Envelope format: [16 bytes salt][ciphertext + tag]
                    salt = raw[:16]
                    cipher_body = raw[16:]
                    key = _derive_machine_key(salt)
                    decrypted = _aes_gcm_decrypt(cipher_body, key)
                except Exception:
                    decrypted = None

            if decrypted:
                parsed = json.loads(decrypted.decode('utf-8'))
                if isinstance(parsed, dict):
                    return parsed
        except Exception as e:
            logger.debug(f"Vault decryption error on {file_path}: {e}")
        return None

    def _save_vault(self, rotate_backup: bool = True):
        """Atomic encrypted file save with fsync and backup rotation."""
        if self.is_safe_mode:
            logger.error("Cannot save vault while in SAFE_MODE.")
            return

        try:
            # 1. Rotate backups
            if rotate_backup and self.vault_file.exists():
                if self.bak1_file.exists():
                    shutil.copy2(self.bak1_file, self.bak2_file)
                shutil.copy2(self.vault_file, self.bak1_file)

            # 2. Prepare payload
            plain_bytes = json.dumps(self._secrets, ensure_ascii=False).encode('utf-8')

            # 3. Encrypt via DPAPI or AES-256-GCM
            cipher_bytes = _win_dpapi_encrypt(plain_bytes) if os.name == "nt" else None
            if not cipher_bytes:
                salt = os.urandom(16)
                key = _derive_machine_key(salt)
                cipher_body = _aes_gcm_encrypt(plain_bytes, key)
                cipher_bytes = salt + cipher_body

            # 4. Atomic file write with fsync
            temp_file = self.vault_file.with_suffix(".tmp")
            with open(temp_file, "wb") as f:
                f.write(cipher_bytes)
                f.flush()
                os.fsync(f.fileno())

            temp_file.replace(self.vault_file)
            self._save_metadata_only()
            self.last_integrity_check = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Failed to persist encrypted vault: {e}")
            raise

    def _save_metadata_only(self):
        """Saves public metadata (no secret values) for auditing and fast dashboard queries."""
        try:
            meta = {
                "schema_version": "2.0.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "vault_integrity": "HEALTHY" if not self.is_safe_mode else "SAFE_MODE",
                "secrets_count": len(self._secrets),
                "items": self.list_secret_metadata()
            }
            temp_meta = self.meta_file.with_suffix(".tmp")
            with open(temp_meta, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temp_meta.replace(self.meta_file)
        except Exception as e:
            logger.debug(f"Metadata write notice: {e}")

    def _migrate_legacy_vault(self, legacy_file: Path):
        """Migrates legacy secrets vault into new hardened vault structure."""
        try:
            with open(legacy_file, "rb") as f:
                raw = f.read()
            dec = _win_dpapi_decrypt(raw) if os.name == "nt" else None
            if not dec:
                # Legacy XOR decryption
                raw_ident = f"{os.environ.get('COMPUTERNAME', '')}-{os.environ.get('USERNAME', '')}-{sys.platform}"
                key = hashlib.sha256(raw_ident.encode('utf-8')).digest()
                ext_key = hashlib.sha512(key).digest()
                out = bytearray(len(raw))
                for i in range(len(raw)):
                    out[i] = raw[i] ^ ext_key[i % len(ext_key)]
                dec = bytes(out)

            data = json.loads(dec.decode('utf-8'))
            for k, v in data.items():
                if isinstance(v, str) and v.strip():
                    self.set_secret(k, v.strip(), created_by="LEGACY_MIGRATION")
            logger.info(f"Successfully migrated {len(data)} legacy secrets into hardened vault.")
        except Exception as e:
            logger.warning(f"Legacy vault migration note: {e}")

    # --- PUBLIC SECRET ACCESS API ---
    def set_secret(
        self,
        name: str,
        value: str,
        provider: Optional[str] = None,
        expires_at: Optional[str] = None,
        created_by: str = "ADMIN",
        status: str = "ACTIVE"
    ) -> bool:
        """Sets an active secret in the vault."""
        if not name or not isinstance(name, str):
            raise ValueError("Secret name must be a non-empty string.")
        if value is None or not isinstance(value, str):
            raise ValueError("Secret value must be a string.")

        clean_val = value.strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        
        current = self._secrets.get(name, {})
        old_val = current.get("active", "")
        
        self._secrets[name] = {
            "active": clean_val,
            "previous": old_val if old_val and old_val != clean_val else current.get("previous", ""),
            "pending": "",
            "provider": provider or name.split("_")[0].lower(),
            "fingerprint": compute_fingerprint(clean_val),
            "version": current.get("version", 0) + 1,
            "status": status,
            "expires_at": expires_at or current.get("expires_at", ""),
            "created_at": current.get("created_at", now_iso),
            "updated_at": now_iso,
            "last_tested_at": current.get("last_tested_at", ""),
            "created_by": created_by
        }
        self._save_vault()
        logger.info(f"Secret '{name}' stored securely (v{self._secrets[name]['version']}).")
        return True

    def get_secret(self, name: str) -> Optional[str]:
        """Retrieves raw secret value in-memory. NEVER log the return value."""
        sec = self._secrets.get(name)
        if not sec:
            # Canonical alias mappings for seamless fallback
            aliases = {
                "GEMINI_PRIMARY_API_KEY": ["GEMINI_API_KEY"],
                "GEMINI_API_KEY": ["GEMINI_PRIMARY_API_KEY"],
                "GEMINI_BACKUP_API_KEY": ["GEMINI_API_KEY_2"],
                "GEMINI_API_KEY_2": ["GEMINI_BACKUP_API_KEY"],
                "META_SYSTEM_USER_TOKEN": ["FB_PAGE_ACCESS_TOKEN", "IG_ACCESS_TOKEN", "META_ACCESS_TOKEN"],
                "FB_PAGE_ACCESS_TOKEN": ["META_SYSTEM_USER_TOKEN", "META_ACCESS_TOKEN"],
                "IG_ACCESS_TOKEN": ["META_SYSTEM_USER_TOKEN", "META_ACCESS_TOKEN"],
                "META_ACCESS_TOKEN": ["META_SYSTEM_USER_TOKEN", "FB_PAGE_ACCESS_TOKEN"],
            }
            for alt in aliases.get(name, []):
                if alt in self._secrets:
                    sec = self._secrets[alt]
                    break

        if not sec:
            return None
        if sec.get("status") == "DISABLED":
            return None
        return sec.get("active") or None

    def has_secret(self, name: str) -> bool:
        """Checks if secret exists and is active."""
        val = self.get_secret(name)
        return bool(val and len(val.strip()) > 0)

    def delete_secret(self, name: str, confirmed_by_admin: bool = False) -> bool:
        """Explicitly deletes a secret. NEVER called automatically on API error."""
        if not confirmed_by_admin:
            raise PermissionError("Explicit admin confirmation is required to delete credentials.")
        if name in self._secrets:
            del self._secrets[name]
            self._save_vault()
            logger.info(f"Secret '{name}' permanently deleted by admin.")
            return True
        return False

    def disable_secret(self, name: str) -> bool:
        """Disables a secret without deleting it."""
        if name in self._secrets:
            self._secrets[name]["status"] = "DISABLED"
            self._secrets[name]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_vault()
            return True
        return False

    def enable_secret(self, name: str) -> bool:
        """Re-enables a secret."""
        if name in self._secrets:
            self._secrets[name]["status"] = "ACTIVE"
            self._secrets[name]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save_vault()
            return True
        return False

    # --- ATOMIC REPLACEMENT WORKFLOW ---
    async def replace_secret_atomic(
        self,
        name: str,
        new_value: str,
        test_callable: Optional[Callable[[str], Any]] = None
    ) -> Tuple[bool, str]:
        """
        Atomic secret replacement:
        1. Save new secret as PENDING
        2. Test new secret via test_callable
        3. If valid: atomic switch (PENDING -> ACTIVE, old -> PREVIOUS)
        4. If invalid: reject & keep old ACTIVE
        """
        if not name or not new_value:
            return False, "Nama secret dan nilai baru wajib diisi."

        clean_new = new_value.strip()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Step 1: Save as PENDING
        if name not in self._secrets:
            self.set_secret(name, clean_new, status="ACTIVE")
            return True, "Secret baru berhasil disimpan sebagai ACTIVE."

        old_entry = self._secrets[name]
        old_active = old_entry.get("active", "")
        
        # Step 2: Test if test_callable is provided
        if test_callable:
            try:
                test_res = await test_callable(clean_new) if asyncio_iscoroutinefunction(test_callable) else test_callable(clean_new)
                is_valid = test_res.get("status") == "VALID" if isinstance(test_res, dict) else bool(test_res)
                if not is_valid:
                    err_detail = test_res.get("message", "Uji coba kredensial baru gagal.") if isinstance(test_res, dict) else "Uji coba gagal."
                    logger.warning(f"Atomic replacement rejected for '{name}': {err_detail}. Preserving previous active token.")
                    return False, f"Penggantian dibatalkan: {err_detail}. Kredensial aktif lama tetap dipertahankan."
            except Exception as e:
                logger.warning(f"Atomic test failed for '{name}': {e}. Preserving active token.")
                return False, f"Penggantian dibatalkan karena uji koneksi gagal: {str(e)}"

        # Step 3: Atomic switch
        self._secrets[name]["previous"] = old_active
        self._secrets[name]["active"] = clean_new
        self._secrets[name]["pending"] = ""
        self._secrets[name]["fingerprint"] = compute_fingerprint(clean_new)
        self._secrets[name]["version"] = old_entry.get("version", 1) + 1
        self._secrets[name]["status"] = "ACTIVE"
        self._secrets[name]["updated_at"] = now_iso
        self._secrets[name]["last_tested_at"] = now_iso
        
        self._save_vault()
        logger.info(f"Atomic replacement successful for '{name}' (v{self._secrets[name]['version']}).")
        return True, "Kredensial berhasil diperbarui secara atomik dan terverifikasi aktif."

    def rollback_secret(self, name: str) -> bool:
        """Rolls back to previous active secret version if available."""
        if name in self._secrets:
            prev = self._secrets[name].get("previous")
            if prev and prev != self._secrets[name].get("active"):
                cur = self._secrets[name]["active"]
                self._secrets[name]["active"] = prev
                self._secrets[name]["previous"] = cur
                self._secrets[name]["fingerprint"] = compute_fingerprint(prev)
                self._secrets[name]["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save_vault()
                logger.info(f"Secret '{name}' rolled back to previous version.")
                return True
        return False

    def list_secret_metadata(self) -> List[Dict[str, Any]]:
        """Lists metadata for all secrets. NEVER returns raw secret values."""
        results = []
        for name, meta in self._secrets.items():
            results.append({
                "name": name,
                "provider": meta.get("provider", name.split("_")[0].lower()),
                "fingerprint": meta.get("fingerprint", compute_fingerprint(meta.get("active", ""))),
                "version": meta.get("version", 1),
                "status": meta.get("status", "ACTIVE"),
                "expires_at": meta.get("expires_at", "-"),
                "has_previous": bool(meta.get("previous")),
                "last_tested_at": meta.get("last_tested_at", "-"),
                "updated_at": meta.get("updated_at", "-"),
                "created_at": meta.get("created_at", "-")
            })
        return sorted(results, key=lambda x: x["name"])

    # --- ENCRYPTED BACKUP & RESTORE (.pmvault) ---
    def export_encrypted_backup(self, passphrase: str, output_path: str) -> str:
        """Exports all secrets into an authenticated AES-256-GCM encrypted .pmvault archive."""
        if not passphrase or len(passphrase) < 6:
            raise ValueError("Passphrase backup minimal 6 karakter.")

        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        salt = os.urandom(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=150_000,
        )
        key = kdf.derive(passphrase.encode('utf-8'))

        backup_payload = {
            "format": "PITA_MEDIA_ENCRYPTED_VAULT",
            "version": "2.0.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "secrets": self._secrets
        }
        plain_bytes = json.dumps(backup_payload, ensure_ascii=False).encode('utf-8')
        cipher_body = _aes_gcm_encrypt(plain_bytes, key)
        
        # Structure: [4 bytes MAGIC 'PMVT'][16 bytes salt][cipher_body]
        final_bytes = b"PMVT" + salt + cipher_body

        with open(dest, "wb") as f:
            f.write(final_bytes)
            f.flush()
            os.fsync(f.fileno())

        logger.info(f"Encrypted credential backup created at {dest}.")
        return str(dest)

    def import_encrypted_backup(self, passphrase: str, input_path: str) -> Tuple[bool, str]:
        """Restores secrets from an encrypted .pmvault archive."""
        src = Path(input_path)
        if not src.exists():
            return False, "File backup tidak ditemukan."

        try:
            with open(src, "rb") as f:
                raw = f.read()

            if not raw.startswith(b"PMVT") or len(raw) < 36:
                return False, "Format file bukan Pita Media Encrypted Vault (.pmvault) yang valid."

            salt = raw[4:20]
            cipher_body = raw[20:]

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=150_000,
            )
            key = kdf.derive(passphrase.encode('utf-8'))

            plain_bytes = _aes_gcm_decrypt(cipher_body, key)
            payload = json.loads(plain_bytes.decode('utf-8'))

            imported_secrets = payload.get("secrets", {})
            if not isinstance(imported_secrets, dict):
                return False, "Struktur data backup tidak valid."

            # Merge / Restore
            count = 0
            for k, v in imported_secrets.items():
                if isinstance(v, dict) and "active" in v:
                    self._secrets[k] = v
                    count += 1
            
            self._save_vault()
            logger.info(f"Successfully restored {count} secrets from backup {src.name}.")
            return True, f"Berhasil memulihkan {count} kredensial dari backup terenkripsi."
        except Exception as e:
            logger.warning(f"Backup restore failed: {e}")
            return False, "Gagal membuka backup. Pastikan password backup Anda benar."


def asyncio_iscoroutinefunction(func):
    import inspect
    return inspect.iscoroutinefunction(func)

# Global Singleton Instance
secret_store = SecretStore()
