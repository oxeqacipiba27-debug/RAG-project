import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import tempfile
import shutil

from aiogram.types import Message, User
from bot.config import Settings
from bot.middlewares.auth import WhitelistMiddleware
from bot.services.db_service import DatabaseService


class TestAuthMiddleware(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_service = DatabaseService(Path(self.temp_dir) / "test.db")
        await self.db_service.init_db()

        self.settings = Settings(
            BOT_TOKEN="fake_token:123",
            ADMIN_IDS={999},
            ALLOWED_TELEGRAM_IDS={100}
        )
        await self.db_service.bootstrap_users(self.settings.ADMIN_IDS, self.settings.ALLOWED_TELEGRAM_IDS)
        self.middleware = WhitelistMiddleware(self.settings, self.db_service)

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_admin_allowed(self):
        handler = AsyncMock(return_value="OK")
        msg = MagicMock(spec=Message)
        admin_user = User(id=999, is_bot=False, first_name="Admin")
        data = {"event_from_user": admin_user}

        result = await self.middleware(handler, msg, data)
        self.assertEqual(result, "OK")
        handler.assert_awaited_once_with(msg, data)

    async def test_whitelisted_user_allowed(self):
        handler = AsyncMock(return_value="OK")
        msg = MagicMock(spec=Message)
        allowed_user = User(id=100, is_bot=False, first_name="Allowed")
        data = {"event_from_user": allowed_user}

        result = await self.middleware(handler, msg, data)
        self.assertEqual(result, "OK")
        handler.assert_awaited_once_with(msg, data)

    async def test_unauthorized_user_denied(self):
        handler = AsyncMock(return_value="OK")
        msg = MagicMock(spec=Message)
        msg.answer = AsyncMock()
        stranger_user = User(id=777, is_bot=False, first_name="Stranger")
        data = {"event_from_user": stranger_user}

        result = await self.middleware(handler, msg, data)
        self.assertIsNone(result)
        handler.assert_not_called()
        msg.answer.assert_awaited_once_with(WhitelistMiddleware.DENIED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
