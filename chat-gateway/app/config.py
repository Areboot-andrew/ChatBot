from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, field_validator
from typing import Optional

OLD_LMSTUDIO_HOST = "192.168.1.84"
NEW_LMSTUDIO_HOST = "192.168.1.85"


def normalize_lmstudio_url(url: str | None) -> str | None:
    if not url:
        return url
    return str(url).replace(OLD_LMSTUDIO_HOST, NEW_LMSTUDIO_HOST)


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"

    # LLM Settings
    LMSTUDIO_URL: str = "http://192.168.1.85:1234/v1"
    LLM_MODEL: str = "gemma-4"
    EMBED_MODEL: str = "bge-m3"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/chatbot"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"

    # Security
    SECRET_KEY: SecretStr = SecretStr("super-secret-key-change-me-in-production")
    FERNET_KEY: SecretStr = SecretStr("super-secret-fernet-key-change-me")
    ADMIN_DEFAULT_PASSWORD: str = "admin123"

    # Public URLs
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # Web search (Google via Serper). Global fallback if a tenant has no own key.
    SERPER_API_KEY: str = "2d030163fbd463059411ab1c1f7ba67220a8510d"

    @field_validator("LMSTUDIO_URL", mode="before")
    @classmethod
    def _normalize_lmstudio_url(cls, value):
        return normalize_lmstudio_url(value)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
