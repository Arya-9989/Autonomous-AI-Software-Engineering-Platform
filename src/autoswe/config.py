"""Runtime configuration, loaded from environment variables (and an optional ``.env``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All settings are read from ``AUTOSWE_``-less upper-case env vars, e.g. ``DATABASE_URL``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- service -----------------------------------------------------------------------------
    environment: Literal["development", "test", "production"] = "production"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    host: str = "0.0.0.0"  # noqa: S104 - container default; override per environment
    port: int = 8000
    # Proxies whose X-Forwarded-* headers are trusted (comma-separated IPs/CIDRs, or "*").
    forwarded_allow_ips: str = "127.0.0.1"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    static_dir: Path | None = None
    metrics_enabled: bool = True

    # --- persistence ---------------------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/autoswe.db"
    db_auto_migrate: bool = True
    db_echo: bool = False

    # --- auth ----------------------------------------------------------------------------------
    # Comma-separated ``principal:key`` pairs, e.g. ``alice:3f9c...,ci:91ab...``.
    api_keys: SecretStr = SecretStr("")
    auth_disabled: bool = False

    # --- worker --------------------------------------------------------------------------------
    worker_enabled: bool = True
    worker_concurrency: int = Field(default=2, ge=1, le=32)
    worker_poll_interval_s: float = Field(default=1.0, gt=0)
    job_stale_after_s: float = Field(default=300.0, gt=0)
    max_active_jobs_per_owner: int = Field(default=3, ge=1)
    max_repair_rounds: int = Field(default=1, ge=0, le=3)

    # --- generated output limits ---------------------------------------------------------------
    max_generated_files: int = Field(default=200, ge=1)
    max_file_bytes: int = Field(default=512_000, ge=1)
    max_total_bytes: int = Field(default=5_000_000, ge=1)

    # --- LLM providers -------------------------------------------------------------------------
    llm_default_provider: Literal["auto", "anthropic", "offline"] = "auto"
    # Demo pacing: the offline provider answers instantly, so live progress is hard to watch.
    # A small delay per call (e.g. 1.5) makes each stage visible. 0 disables it.
    offline_delay_s: float = Field(default=0.0, ge=0, le=10)
    anthropic_api_key: SecretStr | None = None
    # Enable the Anthropic provider without an explicit key (e.g. `ant auth login` profiles).
    anthropic_enabled: bool = False
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Effort | None = None  # None = per-agent defaults
    anthropic_max_tokens: int = Field(default=64_000, ge=1_024, le=128_000)
    anthropic_timeout_s: float = Field(default=900.0, gt=0)
    anthropic_max_retries: int = Field(default=2, ge=0, le=10)
    anthropic_server_fallbacks: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _check_auth(self) -> Settings:
        if not self.auth_disabled and not self.parsed_api_keys():
            raise ValueError(
                "API_KEYS must contain at least one 'principal:key' pair "
                "(or set AUTH_DISABLED=true for local development)."
            )
        return self

    def parsed_api_keys(self) -> dict[str, str]:
        """Return ``{key: principal}`` parsed from ``API_KEYS``."""
        keys: dict[str, str] = {}
        raw = self.api_keys.get_secret_value()
        for entry in filter(None, (part.strip() for part in raw.split(","))):
            principal, sep, key = entry.partition(":")
            if not sep or not principal.strip() or len(key.strip()) < 16:
                raise ValueError(
                    "Each API_KEYS entry must look like 'principal:key' with a key of "
                    "at least 16 characters."
                )
            keys[key.strip()] = principal.strip()
        return keys

    @property
    def anthropic_configured(self) -> bool:
        has_key = self.anthropic_api_key is not None and bool(
            self.anthropic_api_key.get_secret_value()
        )
        return has_key or self.anthropic_enabled

    @property
    def resolved_static_dir(self) -> Path | None:
        candidate = self.static_dir or _REPO_ROOT / "frontend" / "dist"
        return candidate if (candidate / "index.html").is_file() else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
