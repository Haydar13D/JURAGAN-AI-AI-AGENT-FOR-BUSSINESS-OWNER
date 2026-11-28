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

    # Google AI Studio / Gemini Image Generation
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_IMAGE_MODEL: str = "imagen-3.0-generate-002"

    # Cloudflare Workers AI Image Generation (Free Tier: FLUX.1 Schnell)
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = None
    CLOUDFLARE_API_TOKEN: Optional[str] = None
    CLOUDFLARE_IMAGE_MODEL: str = "@cf/black-forest-labs/flux-1-schnell"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_DEFAULT_CHAT_ID: Optional[int] = None

    # WhatsApp Integration (T19)
    WHATSAPP_SANDBOX: bool = True
    WHATSAPP_API_URL: Optional[str] = None
    WHATSAPP_API_TOKEN: Optional[str] = None

    @field_validator("TELEGRAM_DEFAULT_CHAT_ID", mode="before")
    @classmethod
    def parse_optional_int(cls, v):
        if v is None or v == "":
            return None
        return int(v)

    @field_validator(
        "ATMOROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "GEMINI_API_KEY",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "WHATSAPP_API_URL",
        "WHATSAPP_API_TOKEN",
        "PROACTIVE_WHATSAPP_RECIPIENT",
        "ASSET_STORAGE_ENDPOINT",
        "ASSET_STORAGE_BUCKET",
        "ASSET_STORAGE_TOKEN",
        "ASSET_PUBLIC_BASE_URL",
        "IMGBB_API_KEY",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_IG_USER_ID",
        mode="before",
    )
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

    # Proactive Agent Scheduler (T21)
    PROACTIVE_SCHEDULE_HOURS: str = "7,21"
    PROACTIVE_OUTLET_IDS: str = "1"
    PROACTIVE_CHANNEL: str = "telegram"
    PROACTIVE_WHATSAPP_RECIPIENT: Optional[str] = None

    # Instagram & Asset Storage (T23/T24)
    INSTAGRAM_SANDBOX: bool = True
    INSTAGRAM_ACCESS_TOKEN: Optional[str] = None
    INSTAGRAM_IG_USER_ID: Optional[str] = None
    ASSET_STORAGE_ENDPOINT: Optional[str] = None
    ASSET_STORAGE_BUCKET: Optional[str] = None
    ASSET_STORAGE_TOKEN: Optional[str] = None
    ASSET_PUBLIC_BASE_URL: Optional[str] = None
    IMGBB_API_KEY: Optional[str] = None

settings = Settings()
