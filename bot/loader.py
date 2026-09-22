"""
schoolX_bot/bot/loader.py — Инициализация экземпляров бота, диспетчера и сервисов.
Бот выступает в роли тонкого клиента и обращается к RAG API через RAGApiClient.
"""

import socket
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from bot.config import settings
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService
from bot.services.queue_manager import QueueManager
from bot.services.rag_client import RAGApiClient


def create_bot_session() -> AiohttpSession:
    """Создает сессию aiohttp с поддержкой IPv4, прокси и устойчивого соединения."""
    sess = AiohttpSession(proxy=settings.TELEGRAM_PROXY or None)
    # Принудительно используем IPv4 для предотвращения ошибок [WinError 121] semaphore timeout в Windows
    sess._connector_init["family"] = socket.AF_INET
    return sess


# Инициализация Telegram Bot с HTML-разметкой по умолчанию
bot_token = settings.BOT_TOKEN or "123456789:AAG_DummyBotTokenForTestingEnvironment"
api_server = (
    TelegramAPIServer.from_base(settings.TELEGRAM_API_SERVER, is_local=False)
    if settings.TELEGRAM_API_SERVER
    else TelegramAPIServer.from_base("https://api.telegram.org", is_local=False)
)
bot = Bot(
    token=bot_token,
    session=create_bot_session(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

# Инициализация Dispatcher
dp = Dispatcher()

# Инициализация локальных сервисов бота (база данных и биллинг)
db_service = DatabaseService(db_path=settings.SQLITE_DB_PATH)
billing_service = BillingService(settings=settings, db_service=db_service)

# Инициализация HTTP-клиента RAG API (тонкий клиент)
rag_client = RAGApiClient(
    base_url=settings.RAG_API_BASE_URL,
    api_key=settings.RAG_API_KEY,
    timeout=settings.RAG_REQUEST_TIMEOUT
)

# Инициализация менеджера очереди взаимодействия с RAG API
queue_manager = QueueManager(
    rag_client=rag_client,
    db_service=db_service,
    settings=settings
)

# Внедрение зависимостей в контекст Dispatcher aiogram 3.x
dp["settings"] = settings
dp["db_service"] = db_service
dp["billing_service"] = billing_service
dp["rag_client"] = rag_client
dp["queue_manager"] = queue_manager
