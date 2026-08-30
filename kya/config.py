"""Environment configuration.

Small on purpose. The only settings here are the ones that cannot live in
`policies/` because they are secrets or deployment facts: rail credentials, the
merchant signing seed, where the database sits. Everything that governs a
*decision* — ceilings, thresholds, evidence floors — lives in `kya/policy.py`
as data, so that a limit change is visible in the audit trail under a policy
version rather than as an undocumented environment difference.

The test-mode guard is repeated here rather than left to the rail client. A
misconfiguration that reaches the client at all has already been loaded, logged
and possibly printed; refusing at the point of reading is the cheaper failure.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from kya.crypto import KeyPair, keypair_from_seed
from kya.obligation.receipt import MerchantIdentity


class ConfigError(RuntimeError):
    """Configuration is missing or unsafe to run with."""


class Settings(BaseSettings):
    """Values read from the environment or a local ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    kya_merchant_id: str = "merch_sandbox_01"
    #: Any string. Hashed to 32 bytes, so an operator does not have to produce
    #: correctly sized key material by hand — the usual reason a signing key
    #: ends up hardcoded in a repository.
    kya_merchant_key_seed: str = ""

    kya_db_path: str = "data/kya.db"

    anthropic_api_key: str = ""
    kya_enable_semantic_verifier: bool = False

    kya_clock_skew_seconds: int = 300
    kya_appeal_window_seconds: int = 900

    @property
    def has_razorpay_credentials(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    def require_test_mode(self) -> None:
        """Refuse anything that is not a Razorpay test key."""
        if self.razorpay_key_id and not self.razorpay_key_id.startswith("rzp_test_"):
            raise ConfigError(
                "RAZORPAY_KEY_ID is not a test key. This gateway is a defensive "
                "demonstration and is only ever run against test mode."
            )

    def merchant_identity(self) -> MerchantIdentity:
        """The key that seals obligation receipts.

        Falls back to a seed derived from the merchant id when none is
        configured, so the system runs out of the box. That fallback is
        deterministic and therefore **not secret** — fine for a sandbox and the
        eval harness, unacceptable anywhere real, which is why it is loud here
        rather than quietly convenient.
        """
        seed_source = self.kya_merchant_key_seed or f"insecure-dev-{self.kya_merchant_id}"
        return MerchantIdentity(
            merchant_id=self.kya_merchant_id,
            keypair=merchant_keypair(self.kya_merchant_id, seed_source),
        )

    @property
    def merchant_key_is_ephemeral(self) -> bool:
        """True when the signing key came from the fallback, not from config."""
        return not self.kya_merchant_key_seed

    def db_path(self) -> str:
        """Ensure the database's directory exists; ``:memory:`` passes through."""
        if self.kya_db_path == ":memory:":
            return self.kya_db_path
        path = Path(self.kya_db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)


def merchant_keypair(merchant_id: str, seed_source: str) -> KeyPair:
    return keypair_from_seed(
        f"{merchant_id}-obligation-key-1",
        hashlib.sha256(seed_source.encode("utf-8")).digest(),
    )


@dataclass(frozen=True, slots=True)
class RailCredentials:
    key_id: str
    key_secret: str
    webhook_secret: str


def rail_credentials(settings: Settings) -> RailCredentials:
    settings.require_test_mode()
    if not settings.has_razorpay_credentials:
        raise ConfigError(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are not set. Copy "
            ".env.example to .env and fill in test-mode keys, or run against "
            "FakeRazorpayClient."
        )
    return RailCredentials(
        key_id=settings.razorpay_key_id,
        key_secret=settings.razorpay_key_secret,
        webhook_secret=settings.razorpay_webhook_secret,
    )


def load_settings() -> Settings:
    settings = Settings()
    settings.require_test_mode()
    return settings
