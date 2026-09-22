import asyncio
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.config import Settings
from bot.handlers import (
    admin_billing_router,
    admin_router,
    subscription_router,
    user_router,
)
from bot.middlewares.subscription_check import SubscriptionMiddleware
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService
from bot.services.ingest import DocumentIngestionService
from bot.services.queue_manager import QueueManager
from bot.services.rag_service import RAGService
from bot.services.vector_db import VectorDBService


class TestCommandsDispatcher(unittest.IsolatedAsyncioTestCase):
    """Тестирование обработки всех команд, кнопок и коллбэков через Dispatcher."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_dispatcher.db"
        self.chroma_path = Path(self.temp_dir) / "chroma"

        self.settings = Settings(
            BOT_TOKEN="fake_token:123",
            ADMIN_IDS={6060196826},
            ALLOWED_TELEGRAM_IDS=set(),
            SQLITE_DB_PATH=self.db_path,
            CHROMA_PERSIST_DIR=self.chroma_path,
        )

        self.db_service = DatabaseService(self.db_path)
        await self.db_service.init_db()

        self.billing_service = BillingService(self.settings, self.db_service)
        self.vector_db = VectorDBService(
            persist_dir=self.chroma_path,
            model_name=self.settings.EMBEDDING_MODEL_NAME,
        )
        await self.vector_db.initialize()

        self.ingest_service = DocumentIngestionService()
        self.rag_service = RAGService(
            settings=self.settings,
            vector_db=self.vector_db,
            openai_client=AsyncMock(),
        )
        self.queue_manager = QueueManager(
            rag_service=self.rag_service,
            db_service=self.db_service,
        )

        self.dp = Dispatcher()
        self.dp["settings"] = self.settings
        self.dp["db_service"] = self.db_service
        self.dp["billing_service"] = self.billing_service
        self.dp["vector_db"] = self.vector_db
        self.dp["ingest_service"] = self.ingest_service
        self.dp["rag_service"] = self.rag_service
        self.dp["queue_manager"] = self.queue_manager

        sub_middleware = SubscriptionMiddleware(
            settings=self.settings,
            db_service=self.db_service,
            billing_service=self.billing_service,
        )
        self.dp.message.middleware(sub_middleware)
        self.dp.callback_query.middleware(sub_middleware)

        self.dp.include_router(admin_router)
        self.dp.include_router(admin_billing_router)
        self.dp.include_router(subscription_router)
        self.dp.include_router(user_router)

        self.mock_bot = AsyncMock(spec=Bot)
        self.mock_bot.id = 123456789
        self.mock_bot.get_me = AsyncMock(
            return_value=User(id=123456789, is_bot=True, first_name="Bot", username="test_bot")
        )
        self.mock_bot.send_message = AsyncMock()
        self.mock_bot.edit_message_text = AsyncMock()
        self.mock_bot.answer_callback_query = AsyncMock()

    async def asyncTearDown(self):
        for r in (admin_router, admin_billing_router, subscription_router, user_router):
            r._parent_router = None
        self.dp.sub_routers.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_all_commands_and_buttons_do_not_crash(self):
        """Проверка, что ни одна команда и ни одна кнопка меню не вызывает исключений."""
        commands_to_test = [
            "/start",
            "/help",
            "/subscribe",
            "/my_sub",
            "/my_files",
            "/clear_my_db",
            "/admin",
            "/admin_billing",
            "/stats",
            "💎 Тарифы и подписка",
            "📋 Моя подписка",
            "📁 Мои документы",
            "🗑 Очистить базу",
            "📖 Справка",
            "🛠 Админ-консоль",
            "💰 Биллинг и чеки",
            "📊 Статистика",
        ]

        admin_user = User(id=6060196826, is_bot=False, first_name="Admin", username="admin")
        admin_chat = Chat(id=6060196826, type="private")

        for cmd in commands_to_test:
            msg = Message(
                message_id=100,
                date=1000,
                chat=admin_chat,
                from_user=admin_user,
                text=cmd,
            ).as_(self.mock_bot)
            upd = Update(update_id=1, message=msg)

            # Should not raise any exception
            await self.dp.feed_update(self.mock_bot, upd)

    async def test_all_callbacks_do_not_crash(self):
        """Проверка, что ключевые коллбэки работают корректно."""
        callbacks_to_test = [
            "sub:tariffs",
            "sub:my_sub",
            "sub:checkout:basic",
            "sub:checkout:pro",
            "user:docs:list",
            "user:close",
            "admin:menu",
            "admin:billing:refresh",
        ]

        admin_user = User(id=6060196826, is_bot=False, first_name="Admin", username="admin")
        admin_chat = Chat(id=6060196826, type="private")
        msg = Message(
            message_id=200,
            date=1000,
            chat=admin_chat,
            from_user=admin_user,
            text="Menu message",
        ).as_(self.mock_bot)

        for cb_data in callbacks_to_test:
            cb = CallbackQuery(
                id="cb_test",
                from_user=admin_user,
                chat_instance="ci_1",
                message=msg,
                data=cb_data,
            ).as_(self.mock_bot)
            upd = Update(update_id=2, callback_query=cb)

            # Should not raise any exception
            await self.dp.feed_update(self.mock_bot, upd)


if __name__ == "__main__":
    unittest.main()
