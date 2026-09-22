from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
import openai

from bot.config import settings
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService
from bot.services.ingest import DocumentIngestionService
from bot.services.queue_manager import QueueManager
from bot.services.rag_service import RAGService
from bot.services.vector_db import VectorDBService

import socket
from aiogram.client.telegram import TelegramAPIServer

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

# Инициализация OpenAI клиента для LM Studio (локальный сервер)
openai_client = openai.AsyncOpenAI(
    base_url=settings.LM_STUDIO_URL,
    api_key=settings.LM_STUDIO_API_KEY
)

# Инициализация сервисов RAG-системы и биллинга
db_service = DatabaseService(db_path=settings.SQLITE_DB_PATH)
billing_service = BillingService(settings=settings, db_service=db_service)
vector_db = VectorDBService(
    persist_dir=settings.CHROMA_PERSIST_DIR,
    model_name=settings.EMBEDDING_MODEL_NAME
)
ingest_service = DocumentIngestionService(
    default_chunk_size=settings.CHUNK_SIZE,
    default_overlap=settings.CHUNK_OVERLAP
)
rag_service = RAGService(
    settings=settings,
    vector_db=vector_db,
    openai_client=openai_client
)
queue_manager = QueueManager(
    rag_service=rag_service,
    db_service=db_service
)

# Внедрение зависимостей в контекст Dispatcher aiogram 3.x
dp["settings"] = settings
dp["db_service"] = db_service
dp["billing_service"] = billing_service
dp["vector_db"] = vector_db
dp["ingest_service"] = ingest_service
dp["rag_service"] = rag_service
dp["queue_manager"] = queue_manager
