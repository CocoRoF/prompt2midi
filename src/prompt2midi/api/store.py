"""File-backed state in P2M_DATA_DIR: admin.json (password hash), settings.json, secrets.json (API keys), usage.json.

Everything is written atomically with mode 0600 (directory 0700). secrets.json is encrypted with Fernet
(key derived from P2M_SECRET_KEY by scrypt) when a secret key is configured, otherwise stored as plain JSON.
API keys never leave this module except through `get_key`, which only the provider layer calls.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .config import AppSettings

log = logging.getLogger("prompt2midi.store")

PROVIDER_IDS = ("anthropic", "openai")
DEFAULT_LIMITS = {"requests_per_minute": 10, "daily_llm_cap": 100, "max_prompt_chars": 1000, "max_retries": 2}
LIMIT_RANGES = {"requests_per_minute": (1, 600), "daily_llm_cap": (0, 100_000), "max_prompt_chars": (1, 2000), "max_retries": (0, 5)}
MIN_KEY_CHARS = 16
MAX_KEY_CHARS = 512


def _defaults() -> dict[str, Any]:
    return {
        "providers": {p: {"model": "", "enabled": False} for p in PROVIDER_IDS},
        "limits": dict(DEFAULT_LIMITS),
        "public_llm": False,
        "default_provider": "rule",
    }


class Store:
    def __init__(self, cfg: AppSettings):
        self.cfg = cfg
        self.dir = Path(cfg.data_dir)
        self._lock = threading.RLock()
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:  # pragma: no cover - e.g. unsupported filesystems
            pass
        self._secrets_cache: dict[str, str] | None = None

    # ---------- low level ----------
    def _path(self, name: str) -> Path:
        return self.dir / name

    def _read(self, name: str) -> Any | None:
        try:
            return json.loads(self._path(name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log.warning("could not read %s (corrupt file); ignoring it", name)
            return None

    def _write(self, name: str, data: Any) -> None:
        path = self._path(name)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    # ---------- admin ----------
    def admin_record(self) -> dict | None:
        with self._lock:
            rec = self._read("admin.json")
            return rec if isinstance(rec, dict) and rec.get("hash") else None

    def set_admin_record(self, rec: dict) -> None:
        with self._lock:
            self._write("admin.json", rec)

    # ---------- settings ----------
    def settings(self) -> dict[str, Any]:
        with self._lock:
            data = _defaults()
            saved = self._read("settings.json")
            if isinstance(saved, dict):
                for pid, cfg in (saved.get("providers") or {}).items():
                    if pid in data["providers"] and isinstance(cfg, dict):
                        data["providers"][pid].update({k: cfg[k] for k in ("model", "enabled") if k in cfg})
                data["limits"].update({k: v for k, v in (saved.get("limits") or {}).items() if k in DEFAULT_LIMITS and isinstance(v, int)})
                data["public_llm"] = bool(saved.get("public_llm", False))
                if saved.get("default_provider") in ("rule",) + PROVIDER_IDS:
                    data["default_provider"] = saved["default_provider"]
            return data

    def save_settings(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._write("settings.json", data)

    def update_settings(self, fn) -> dict[str, Any]:
        with self._lock:
            data = self.settings()
            fn(data)
            self.save_settings(data)
            return data

    # ---------- secrets ----------
    def _fernet(self, salt: bytes):
        from cryptography.fernet import Fernet

        raw = hashlib.scrypt(self.cfg.secret_key.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return Fernet(base64.urlsafe_b64encode(raw))

    @property
    def secrets_encrypted(self) -> bool:
        """True when new writes are encrypted (a secret key is configured)."""
        return bool(self.cfg.secret_key)

    def _load_keys(self) -> dict[str, str]:
        rec = self._read("secrets.json")
        if not isinstance(rec, dict):
            return {}
        if rec.get("encrypted"):
            if not self.cfg.secret_key:
                log.warning("secrets.json is encrypted but P2M_SECRET_KEY is not set; stored API keys are unavailable")
                return {}
            try:
                from cryptography.fernet import InvalidToken

                try:
                    plain = self._fernet(base64.b64decode(rec["salt"])).decrypt(rec["token"].encode("ascii"))
                except InvalidToken:
                    log.warning("secrets.json could not be decrypted with the current P2M_SECRET_KEY; stored API keys are unavailable")
                    return {}
                keys = json.loads(plain)
            except (KeyError, ValueError, TypeError):
                log.warning("secrets.json is malformed; ignoring it")
                return {}
        else:
            keys = rec.get("keys") or {}
        return {k: v for k, v in keys.items() if k in PROVIDER_IDS and isinstance(v, str) and v}

    def _save_keys(self, keys: dict[str, str]) -> None:
        if self.cfg.secret_key:
            from cryptography.fernet import Fernet  # noqa: F401  (import check)

            salt = os.urandom(16)
            token = self._fernet(salt).encrypt(json.dumps(keys).encode("utf-8")).decode("ascii")
            rec = {"version": 1, "encrypted": True, "kdf": "scrypt-n16384-r8-p1", "salt": base64.b64encode(salt).decode("ascii"), "token": token}
        else:
            rec = {"version": 1, "encrypted": False, "keys": keys}
        self._write("secrets.json", rec)

    def keys(self) -> dict[str, str]:
        with self._lock:
            return dict(self._load_keys())

    def get_key(self, provider_id: str) -> str | None:
        return self.keys().get(provider_id)

    def set_key(self, provider_id: str, key: str) -> None:
        with self._lock:
            keys = self._load_keys()
            keys[provider_id] = key
            self._save_keys(keys)

    def delete_key(self, provider_id: str) -> bool:
        with self._lock:
            keys = self._load_keys()
            existed = keys.pop(provider_id, None) is not None
            self._save_keys(keys)
            return existed

    def key_hint(self, provider_id: str) -> str:
        key = self.get_key(provider_id)
        return ("…" + key[-4:]) if key else ""

    # ---------- daily LLM usage ----------
    def usage_today(self, day: str) -> int:
        with self._lock:
            rec = self._read("usage.json")
            return int(rec["count"]) if isinstance(rec, dict) and rec.get("day") == day and isinstance(rec.get("count"), int) else 0

    def add_usage(self, day: str, n: int) -> int:
        with self._lock:
            total = self.usage_today(day) + n
            self._write("usage.json", {"day": day, "count": total})
            return total
