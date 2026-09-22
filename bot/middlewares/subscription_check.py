from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from loguru import logger

from bot.config import Settings
from bot.keyboards.menu_kb import ALL_MENU_BUTTONS
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService


class SubscriptionMiddleware(BaseMiddleware):
    """Middleware для контроля подписки пользователей перед обращением к RAG и загрузкой документов.
    
    1. Администраторы (ADMIN_IDS) пропускаются безусловно (байпас).
    2. Служебные команды (/start, /help, /subscribe, /my_sub), кнопки меню и FSM-состояния
       доступны всем пользователям.
    3. Коллбэки подписки (sub:*) и оценок (fb:*) доступны всем.
    4. Обычным пользователям для текстовых запросов к RAG и загрузки файлов
       требуется активная подписка.
    """

    PAYWALL_MESSAGE = (
        "🔒 <b>Для использования персональной RAG-системы требуется активная подписка.</b>\n\n"
        "Оформите подписку, чтобы загружать документы и задавать вопросы нейросети по вашим файлам.\n\n"
        "🔥 <i>Скидка 40% по тарифу Early-Bird для первых 10 клиентов!</i>"
    )

    PUBLIC_COMMANDS = {"/start", "/help", "/subscribe", "/my_sub"} | ALL_MENU_BUTTONS

    def __init__(
        self,
        settings: Settings,
        db_service: DatabaseService,
        billing_service: BillingService
    ):
        super().__init__()
        self.settings = settings
        self.db_service = db_service
        self.billing_service = billing_service

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

        # 1. Администраторы системы всегда имеют доступ
        if user_id in self.settings.ADMIN_IDS:
            return await handler(event, data)

        # 2. Пропуск активного FSM-состояния (например, загрузка чека)
        state: FSMContext = data.get("state")
        if state:
            current_state = await state.get_state()
            if current_state:
                return await handler(event, data)

        # 3. Обработка CallbackQuery
        if isinstance(event, CallbackQuery):
            cb_data = event.data or ""
            # Разрешаем коллбэки подписки, оценок фидбека и закрытия
            if any(cb_data.startswith(prefix) for prefix in ("sub:", "fb:", "user:close", "user:noop")):
                return await handler(event, data)

        # 4. Обработка Message
        if isinstance(event, Message):
            text = (event.text or "").strip()
            # Пропуск открытых информационных команд
            for cmd in self.PUBLIC_COMMANDS:
                if text == cmd or text.startswith(f"{cmd} ") or text.startswith(f"{cmd}@"):
                    return await handler(event, data)

            # Пропуск системных сообщений об успешной оплате Telegram Payments
            if event.successful_payment:
                return await handler(event, data)

        # 5. Проверка активной подписки пользователя
        has_access = await self.billing_service.has_active_access(user_id)
        if has_access:
            return await handler(event, data)

        # 6. Доступ заблокирован — вывод paywall с кнопкой выбора тарифа
        logger.info(
            f"Blocked RAG access (no subscription) for user {user_id} (@{user.username or 'no_user'})"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Выбрать тариф (со скидкой 40%)",
                        callback_data="sub:tariffs"
                    )
                ]
            ]
        )

        if isinstance(event, Message):
            await event.answer(self.PAYWALL_MESSAGE, reply_markup=keyboard)
        elif isinstance(event, CallbackQuery):
            await event.answer("Требуется активная подписка.", show_alert=True)

        return None
