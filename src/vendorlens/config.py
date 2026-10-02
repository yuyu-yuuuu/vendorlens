from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VENDORLENS_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./data/vendorlens.db"
    redis_url: str = "redis://localhost:6380/0"
    chroma_url: str = "http://localhost:8100"
    storage_root: Path = Path("data/files")
    model_provider: Literal["openai_compatible", "deepseek"] = "openai_compatible"
    model_base_url: str | None = None
    model_api_key: SecretStr | None = None
    model_name: str | None = None
    model_timeout_seconds: float = 60.0
    model_max_tool_rounds: int = 4


@lru_cache
def get_settings() -> Settings:
    return Settings()
