"""Settings of the bank connector, all from the environment (the repository's ``.env`` is loaded by the gateway)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SECRETS_DIR = Path.home() / ".finance-assistant" / "secrets"


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass
class BankingConfig:
    # the Enable Banking application: its id and the RSA private key whose certificate was uploaded to the control panel
    app_id: str | None = field(default_factory=lambda: os.getenv("ENABLE_BANKING_APP_ID") or None)
    key_path: Path = field(default_factory=lambda: Path(_env("ENABLE_BANKING_KEY_PATH", str(DEFAULT_SECRETS_DIR / "enablebanking.pem"))).expanduser())
    api_url: str = field(default_factory=lambda: _env("ENABLE_BANKING_API_URL", "https://api.enablebanking.com"))
    secrets_dir: Path = field(default_factory=lambda: Path(_env("FINANCE_SECRETS_DIR", str(DEFAULT_SECRETS_DIR))).expanduser())
    gateway_url: str = field(default_factory=lambda: _env("FINANCE_GATEWAY_URL", "http://127.0.0.1:8000").rstrip("/"))
    ui_url: str = field(default_factory=lambda: _env("FINANCE_UI_URL", "http://127.0.0.1:3000").rstrip("/"))
    redirect_url: str | None = field(default_factory=lambda: os.getenv("ENABLE_BANKING_REDIRECT_URL") or None)
    consent_days: int = field(default_factory=lambda: int(_env("ENABLE_BANKING_CONSENT_DAYS", "180")))
    language: str = field(default_factory=lambda: _env("ENABLE_BANKING_LANGUAGE", "it"))
    sync_mode: str = field(default_factory=lambda: _env("FINANCE_BANK_SYNC", "auto").lower())  # auto | off
    sync_interval_hours: float = field(default_factory=lambda: float(_env("FINANCE_BANK_SYNC_INTERVAL_HOURS", "12")))
    initial_history_days: int = 89  # beyond 90 days many banks ask for another SCA
    overlap_days: int = 7  # re-fetch a few days before the last known transaction: late bookings, corrections

    @property
    def callback_url(self) -> str:
        return self.redirect_url or f"{self.gateway_url}/banking/callback"

    def configured(self) -> bool:
        return bool(self.app_id) and self.key_path.exists()

    def status(self) -> dict:
        return {
            "configured": self.configured(),
            "app_id_set": bool(self.app_id),
            "key_found": self.key_path.exists(),
            "key_path": str(self.key_path),
            "redirect_url": self.callback_url,
            "sync_mode": self.sync_mode,
            "sync_interval_hours": self.sync_interval_hours,
            "consent_days": self.consent_days,
            "secrets_dir": str(self.secrets_dir),
        }

    def private_key(self) -> bytes:
        return self.key_path.read_bytes()
