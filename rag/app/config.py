from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql://ledger_app:dev_only@localhost:5432/ledger"
    kafka_brokers: str = "localhost:9092"
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    log_level: str = "INFO"
    rag_top_k: int = 8
    chunk_size: int = 400
    chunk_overlap: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
