from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from typing import Optional

class Settings(BaseSettings):
    ENVIRONMENT: str = "development"

    # LLM Settings
    LMSTUDIO_URL: str = "http://192.168.1.84:1234/v1"
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
