from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://ledger_app:dev_only@localhost:5432/ledger"
    redis_url: str = "redis://localhost:6379"
    ledger_core_addr: str = "localhost:50051"
    recon_engine_path: str = "/app/recon_engine"
    log_level: str = "INFO"
    rate_limit_per_sec: int = 50
    idempotency_ttl_seconds: int = 86400
    kafka_brokers: str = "localhost:9092"
    webhook_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
