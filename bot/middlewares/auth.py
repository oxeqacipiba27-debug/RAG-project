from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from bot.config import Settings
from bot.services.db_service import DatabaseService


class WhitelistMiddleware(BaseMiddleware):
    """Middleware для строгой проверки прав доступа к боту (Whitelist).
    
    Пропускает только:
    1. Администраторов из settings.ADMIN_IDS.
    2. Разрешенных пользователей из базы данных (db_service.is_user_allowed).
    
    Неавторизованным пользователям выдается уведомление о запрете доступа.
    """

    DENIED_MESSAGE = "🚫 <b>Доступ ограничен.</b>\nОбратитесь к администратору для подключения."

    def __init__(self, settings: Settings, db_service: DatabaseService):
        super().__init__()
        self.settings = settings
        self.db_service = db_service

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        user_id = user.id

        # 1. Системные администраторы всегда имеют доступ
        if user_id in self.settings.ADMIN_IDS:
            return await handler(event, data)

        # 2. Проверка по базе данных (с in-memory кэшем)
        if await self.db_service.is_user_allowed(user_id):
            return await handler(event, data)

        # 3. Доступ запрещен
        logger.warning(
            f"Unauthorized access attempt by user {user_id} (@{user.username or 'no_user'})"
        )

        if isinstance(event, Message):
            await event.answer(self.DENIED_MESSAGE)
        elif isinstance(event, CallbackQuery):
            await event.answer(self.DENIED_MESSAGE.replace("<b>", "").replace("</b>", ""), show_alert=True)

        return None
