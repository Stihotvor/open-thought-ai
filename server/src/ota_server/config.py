"""Server settings, read from environment variables with the OTA_ prefix."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTA_", env_file=".env", extra="ignore")

    device_id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    # SHA-256 hex digest of the device token. The plain token is never stored on the server.
    device_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: str = Field(default="en", pattern=r"^(en|pl|uk)$")
    volume: int = Field(default=60, ge=0, le=100)
    # Turn starts per device: protects the provider quota from a stuck button or a replay loop.
    turns_per_minute: float = Field(default=6, gt=0, le=600)
    turn_burst: int = Field(default=3, ge=1, le=20)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
