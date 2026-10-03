from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    base_url: str
    api_key: SecretStr
    model: str = Field(min_length=1)
    provider: Literal["openai_compatible", "deepseek"] = "openai_compatible"
    timeout_seconds: float = Field(default=60, gt=0)
    base_score: float = Field(default=100, ge=0, le=200)
    input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    price_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VENDORLENS_", env_file=".env", extra="ignore")

    api_access_key: SecretStr | None = None
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
    upload_max_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    task_lease_seconds: int = Field(default=300, ge=15)
    embedding_provider: Literal["hash", "local"] = "hash"
    embedding_model_path: Path | None = None
    reranker_model_path: Path | None = None
    model_input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    model_output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    model_price_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    model_endpoints: list[ModelEndpoint] = Field(default_factory=list)
    routing_window_seconds: int = Field(default=900, ge=1)
    routing_min_samples: int = Field(default=3, ge=1)
    routing_cooldown_seconds: int = Field(default=60, ge=1)
    routing_probe_seconds: int = Field(default=120, ge=1)
    routing_latency_target_ms: float = Field(default=5000, gt=0)
    skills_root: Path = Path("skills")
    memory_redis_enabled: bool = False
    memory_chroma_enabled: bool = False
    memory_ttl_seconds: int = Field(default=86400, ge=1)
    memory_recent_messages: int = Field(default=6, ge=2, le=30)
    memory_compress_at: int = Field(default=16, ge=4)

    @model_validator(mode="after")
    def validate_runtime(self):
        if len({e.id for e in self.model_endpoints}) != len(self.model_endpoints):
            raise ValueError("model endpoint ids must be unique")
        if self.memory_compress_at <= self.memory_recent_messages:
            raise ValueError("memory compression threshold must exceed recent messages")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
