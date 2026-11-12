"""Server settings, read from environment variables with the OTA_ prefix."""

import os
from functools import lru_cache
from typing import Literal

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTA_", env_file=ENV_FILE, extra="ignore")

    device_id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    # SHA-256 hex digest of the device token. The plain token is never stored on the server.
    device_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: str = Field(default="en", pattern=r"^(en|pl|uk)$")
    volume: int = Field(default=60, ge=0, le=100)
    # Turn starts per device: protects the provider quota from a stuck button or a replay loop.
    turns_per_minute: float = Field(default=6, gt=0, le=600)
    turn_burst: int = Field(default=3, ge=1, le=20)
    # Reply source: "echo" (M2 test mode) or "voice" (STT -> chat -> TTS, needs the model roles).
    # Echo stays the default until moderation exists (docs/security.md).
    pipeline: Literal["echo", "voice"] = "echo"
    tts_voice: str = Field(default="", max_length=100)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment


def model_env() -> dict[str, str]:
    """The environment for endpoints.load_roles, read the same way as Settings:
    process variables win over the env file."""
    from_file = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
    return {**from_file, **os.environ}
