from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080
    ANTHROPIC_API_KEY: str | None = None
    PURCHASE_AI_MODEL: str = "claude-opus-5-5"
    STATEMENT_PARSER_MODEL: str = "claude-sonnet-5"
    MAX_FILE_SIZE_MB: int = 20
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    # Import inbox: a Gmail account users forward their bank emails to, read over IMAP
    EMAIL_IMPORT_ADDRESS: str | None = None
    EMAIL_IMPORT_APP_PASSWORD: str | None = None
    EMAIL_IMPORT_IMAP_HOST: str = "imap.gmail.com"
    EMAIL_IMPORT_CRON_SECRET: str | None = None
    # Fernet key that encrypts stored bank PDF passwords; derived from SECRET_KEY if unset
    SECRET_BOX_KEY: str | None = None
    EXTERNAL_IMPORT_SECRET: str | None = None
    EXTERNAL_IMPORT_ALLOWED_EMAIL: str | None = None

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
