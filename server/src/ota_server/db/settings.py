"""Database settings, read from environment variables with the OTA_ prefix."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ota_server.config import ENV_FILE
from ota_server.db.names import DEVICE_ID_PATTERN


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTA_", env_file=ENV_FILE, extra="ignore")

    device_id: str = Field(pattern=f"^{DEVICE_ID_PATTERN.pattern}$")
    # Direct Postgres URL (never a pooler). Secret: hidden from repr and str. None: no database.
    database_url: SecretStr | None = None
