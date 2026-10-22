from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_NAME: str = "JuraganAI"
    APP_ENV: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8005
    DEBUG: bool = True

    # Database URL
    DATABASE_URL: str = "sqlite+aiosqlite:///./juragan_ai.db"

    # LLM Providers Configuration
    LLM_PROVIDER: str = "atmorouter"  # "atmorouter", "deepseek", atau "openrouter"

    # AtmoRouter LLM (Supports free model: atmo/deepseek-v4.1-flash)
    ATMOROUTER_API_KEY: Optional[str] = None
    ATMOROUTER_BASE_URL: str = "https://atmorouter.dev/v1"
    ATMOROUTER_MODEL: str = "atmo/deepseek-v4.1-flash"

    # DeepSeek LLM
    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # OpenRouter LLM
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_MODEL: str = "deepseek/deepseek-chat"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_DEFAULT_CHAT_ID: Optional[int] = None

    @field_validator("TELEGRAM_DEFAULT_CHAT_ID", mode="before")
    @classmethod
    def parse_optional_int(cls, v):
        if v is None or v == "":
            return None
        return int(v)

    @field_validator("ATMOROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN", mode="before")
    @classmethod
    def parse_optional_str(cls, v):
        if v == "":
            return None
        return v

    # Business Defaults
    DEFAULT_OUTLET_ID: int = 1
    DAILY_REPORT_CRON_HOUR: int = 22
    DAILY_REPORT_CRON_MINUTE: int = 30
    TIMEZONE: str = "Asia/Jakarta"

settings = Settings()
