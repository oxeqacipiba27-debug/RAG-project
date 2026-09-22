"""
schoolX_bot/bot/config.py — Конфигурация Telegram-бота (тонкий клиент).
Все параметры инференса, модели, retrieval, эмбеддингов и генерации
управляются исключительно на стороне RAG-приложения.
"""

from pathlib import Path
from typing import Any, Optional, Set
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Конфигурация приложения, загружаемая из переменных окружения и .env файла."""

    model_config = SettingsConfigDict(
        env_file=_PROJECT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # ==========================================================================
    # Telegram Bot
    # ==========================================================================
    BOT_TOKEN: str = ""
    ADMIN_IDS: Any = set()
    ALLOWED_TELEGRAM_IDS: Any = set()
    TELEGRAM_PROXY: str = ""
    TELEGRAM_API_SERVER: str = ""

    # ==========================================================================
    # RAG API Connection Settings (Strict Thin Client)
    # Все параметры генерации и retrieval находятся на стороне RAG API!
    # ==========================================================================
    RAG_API_BASE_URL: str = Field(default="http://localhost:8000", description="Базовый URL RAG-сервиса")
    RAG_API_KEY: Optional[str] = Field(default=None, description="API-ключ для авторизации в RAG API (если требуется)")
    RAG_REQUEST_TIMEOUT: float = Field(default=60.0, description="Таймаут запросов к RAG API в секундах")
    RAG_STREAM_ENABLED: bool = Field(default=True, description="Флаг использования потокового вывода (SSE)")

    # ==========================================================================
    # Paths & Storage
    # ==========================================================================
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    DOCS_STORAGE_DIR: Path = Path("data/documents")
    SQLITE_DB_PATH: Path = Path("data/bot.db")

    # ==========================================================================
    # Billing & Subscription
    # ==========================================================================
    PAYMENT_MODE: str = "MANUAL"  # "MANUAL", "TELEGRAM_PAYMENTS"
    PAYMENT_PROVIDER_TOKEN: str = ""
    MANUAL_PAYMENT_DETAILS: str = (
        "💳 <b>Реквизиты для оплаты (перевод на карту):</b>\n\n"
        "• <b>Банк:</b> Т-Банк\n"
        "• <b>Номер телефона (СБП):</b> <code>+7 (965) 207-34-90</code>\n"
        "• <b>Номер карты:</b> <code>2200 7019 5296 4374</code>\n"
        "• <b>Получатель:</b> Юрий А.\n\n"
    )
    PRICE_BASIC_FULL: int = 3500
    PRICE_BASIC_PROMO: int = 2100
    PRICE_PRO_FULL: int = 5500
    PRICE_PRO_PROMO: int = 3300
    EARLY_BIRD_LIMIT: int = 10
    SUBSCRIPTION_DAYS: int = 30

    # ==========================================================================
    # Logging
    # ==========================================================================
    LOG_LEVEL: str = "INFO"

    @field_validator("ADMIN_IDS", "ALLOWED_TELEGRAM_IDS", mode="before")
    @classmethod
    def parse_ids_set(cls, value: Any) -> Set[int]:
        """Парсинг списка Telegram ID из строки с запятыми, JSON или списка чисел."""
        if value is None:
            return set()
        if isinstance(value, set):
            return {int(x) for x in value}
        if isinstance(value, (list, tuple)):
            return {int(x) for x in value}
        if isinstance(value, (int, float)):
            return {int(value)}
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return set()
            ids = set()
            for part in value.replace("[", "").replace("]", "").split(","):
                part = part.strip()
                if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                    ids.add(int(part))
            return ids
        return set()

    @field_validator("DOCS_STORAGE_DIR", "SQLITE_DB_PATH", mode="after")
    @classmethod
    def resolve_paths(cls, path_val: Path) -> Path:
        """Приведение относительных путей к абсолютному пути проекта."""
        if not path_val.is_absolute():
            base_dir = Path(__file__).resolve().parent.parent
            return base_dir / path_val
        return path_val

    def ensure_directories(self) -> None:
        """Создание необходимых директорий при старте бота."""
        self.DOCS_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
cfg = settings
