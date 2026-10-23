"""Read-only Groww credential loading from the environment / local .env.

PAPER TRADING — NO REAL ORDERS. This module only *reads* credentials so a
future read-only data adapter (historical candles, instruments, quotes) can
authenticate. It makes no network call and nothing in the app uses it to place
an order; there is no live-trading flag on purpose.

Secrets are held as ``SecretStr`` so ``repr``/logging show ``**********``.
Auth modes (see docs/FINDINGS_INTRADAY_GROWW.md): any one of
  1. GROWW_ACCESS_TOKEN                     (expires daily, 6:00 AM per Groww)
  2. GROWW_API_KEY + GROWW_API_SECRET       (daily approval on Groww)
  3. GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET   (TOTP; Groww's pages disagree on expiry)
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, SecretStr

_FIELDS = {
    "GROWW_ACCESS_TOKEN": "access_token",
    "GROWW_API_KEY": "api_key",
    "GROWW_API_SECRET": "api_secret",
    "GROWW_TOTP_TOKEN": "totp_token",
    "GROWW_TOTP_SECRET": "totp_secret",
}


class GrowwCredentials(BaseModel):
    access_token: SecretStr | None = None
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    totp_token: SecretStr | None = None
    totp_secret: SecretStr | None = None

    def auth_mode(self) -> str | None:
        """Which flow the supplied values complete, or None if none does."""
        if self.access_token:
            return "access_token"
        if self.api_key and self.api_secret:
            return "api_key_secret"
        if self.totp_token and self.totp_secret:
            return "totp"
        return None

    def present(self) -> dict[str, bool]:
        """Which variables are set. Never returns values."""
        return {env: getattr(self, attr) is not None for env, attr in _FIELDS.items()}


def load_groww_credentials(env_file: str | Path | None = ".env") -> GrowwCredentials:
    """Load from the process environment, after an optional local .env file.

    Real environment variables win over the file. Blank values count as unset.
    """
    if env_file is not None and Path(env_file).is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    values = {}
    for env, attr in _FIELDS.items():
        raw = os.environ.get(env, "").strip()
        if raw:
            values[attr] = SecretStr(raw)
    return GrowwCredentials(**values)
