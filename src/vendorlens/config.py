from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VENDORLENS_", env_file=".env", extra="ignore"
    )

    environment: str = "development"
    database_url: str = "sqlite:///./data/vendorlens.db"
    redis_url: str = "redis://localhost:6380/0"
    chroma_url: str = "http://localhost:8100"
    storage_root: Path = Path("data/files")


@lru_cache
def get_settings() -> Settings:
    return Settings()
