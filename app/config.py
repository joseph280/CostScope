"""
Centralized configuration for CostScope.

Configuration is read once from environment variables (loaded from .env via
python-dotenv) and exposed as a frozen Pydantic model. Call sites import the
`config` singleton rather than reading os.environ directly — this gives us:

  - One place to change defaults
  - Type-checked values (no string-vs-int bugs)
  - Validation at startup (missing key fails fast, not at first call)
  - A natural seam to swap env-based config for AWS Secrets Manager, Vault,
    or internal config service without touching call sites.

Pricing data is intentionally kept here rather than in the wrapper. Prices
change; wrappers shouldn't ship updates for a dollar value change.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


# ---------------------------------------------------------------------------
# Pricing reference table (USD per 1M tokens)
# ---------------------------------------------------------------------------
# Source: https://www.anthropic.com/pricing (verified May 2026)
# In production this would be either:
#   (a) refreshed from a config service / feature flag system, or
#   (b) versioned alongside model deployments (i.e. each model version pins
#       its own price at deploy time so old logs stay accurate).
ANTHROPIC_PRICING_PER_MTOK: Dict[str, Dict[str, float]] = {
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00},
    "claude-opus-4-1":   {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":  {"input": 0.80,  "output": 4.00},
}


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """All runtime configuration in one typed object."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Anthropic ----------------------------------------------------------
    anthropic_api_key: str = Field(..., description="Required. Anthropic API key.")
    anthropic_model: str = Field(
        default="claude-sonnet-4-5",
        description="Default Claude model. Override per-call when needed.",
    )
    anthropic_max_retries: int = Field(default=3, ge=1, le=10)
    anthropic_base_backoff_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    anthropic_request_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)

    # --- Snowflake (used later in snowflake_wrapper.py) ---------------------
    snowflake_account: str = Field(default="", description="e.g. xy12345.us-west-2")
    snowflake_user: str = Field(default="")
    snowflake_password: str = Field(default="")
    snowflake_warehouse: str = Field(default="COMPUTE_WH")
    snowflake_database: str = Field(default="COSTSCOPE")
    snowflake_schema: str = Field(default="DEMO")

    # --- App ---------------------------------------------------------------
    log_level: str = Field(default="INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Singleton accessor. Cached so we don't re-parse env on every import.
    `lru_cache` makes this safe in multi-threaded contexts too.
    """
    return Settings()


# Convenience: a module-level `settings` import works for most call sites.
settings = get_settings()