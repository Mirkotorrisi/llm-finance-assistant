"""Encrypted store for what grants access to the banks: the aggregator's session ids and the pending consents.

A Fernet key is generated once in the secrets folder (mode 0600, folder 0700) and the data file next to it holds
only ciphertext. The database never sees a session id: it stores a ``session_ref`` that indexes this store.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

PENDING_TTL_SECONDS = 3600


class SecretStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.key_file = self.directory / "store.key"
        self.data_file = self.directory / "banking.enc"

    # ------------------------------------------------------------------ plumbing

    def _fernet(self) -> Fernet:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        if not self.key_file.exists():
            self.key_file.write_bytes(Fernet.generate_key())
            os.chmod(self.key_file, 0o600)
        return Fernet(self.key_file.read_bytes())

    def _load(self) -> dict[str, Any]:
        if not self.data_file.exists():
            return {"sessions": {}, "pending": {}}
        raw = self._fernet().decrypt(self.data_file.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        data.setdefault("sessions", {})
        data.setdefault("pending", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        token = self._fernet().encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        tmp = self.data_file.with_suffix(".tmp")
        tmp.write_bytes(token)
        os.chmod(tmp, 0o600)
        tmp.replace(self.data_file)

    # ------------------------------------------------------------------ sessions

    def get_session(self, ref: str) -> dict[str, Any] | None:
        return self._load()["sessions"].get(ref)

    def put_session(self, ref: str, value: dict[str, Any]) -> None:
        data = self._load()
        data["sessions"][ref] = value
        self._save(data)

    def delete_session(self, ref: str) -> None:
        data = self._load()
        if data["sessions"].pop(ref, None) is not None:
            self._save(data)

    # ------------------------------------------------------------------ pending consents (state -> context)

    def put_pending(self, state: str, value: dict[str, Any]) -> None:
        data = self._load()
        now = time.time()
        data["pending"] = {k: v for k, v in data["pending"].items() if now - v.get("created", now) < PENDING_TTL_SECONDS}
        data["pending"][state] = {**value, "created": now}
        self._save(data)

    def pop_pending(self, state: str) -> dict[str, Any] | None:
        data = self._load()
        value = data["pending"].pop(state, None)
        if value is not None:
            self._save(data)
            if time.time() - value.get("created", 0) >= PENDING_TTL_SECONDS:
                return None
        return value
