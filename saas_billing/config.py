from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from saas_billing.core.dunning import DEFAULT_SCHEDULE_DAYS, parse_schedule


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", pattern="^(development|test|staging|production)$")
    log_level: str = "INFO"
    database_url: str = "sqlite:///./billing.db"
    api_key: str | None = None

    default_currency: str = Field(default="BDT", min_length=3, max_length=3)
    vat_rate_bps: int = Field(default=1500, ge=0, le=10_000)

    payment_gateway: str = Field(default="mock", pattern="^(mock|stripe_like|bkash_like)$")
    gateway_api_key: str | None = None
    gateway_base_url: str | None = None

    dunning_schedule_days: str = ",".join(str(d) for d in DEFAULT_SCHEDULE_DAYS)
    invoice_due_days: int = Field(default=7, ge=0)
    invoice_number_prefix: str = "INV"

    webhook_timeout_seconds: float = 5.0

    @field_validator("dunning_schedule_days")
    @classmethod
    def _valid_schedule(cls, value: str) -> str:
        parse_schedule(value)
        return value

    @property
    def dunning_schedule(self) -> tuple[int, ...]:
        return parse_schedule(self.dunning_schedule_days)


@lru_cache
def get_settings() -> Settings:
    return Settings()
