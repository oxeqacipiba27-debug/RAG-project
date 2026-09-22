import asyncio
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, User

from bot.config import Settings
from bot.handlers.admin import AdminUserStates, cb_admin_user_add_prompt, handle_admin_user_id_input
from bot.handlers.admin_billing import (
    AdminBillingStates,
    cb_admin_billing_grant_prompt,
    handle_admin_grant_input,
)
from bot.handlers.subscription import cmd_my_sub, cmd_subscribe
from bot.handlers.user import cmd_clear_my_db, cmd_help, cmd_my_files, cmd_start
from bot.keyboards.menu_kb import (
    ALL_MENU_BUTTONS,
    BTN_ADMIN_BILLING,
    BTN_ADMIN_CONSOLE,
    BTN_ADMIN_STATS,
    BTN_CLEAR_DB,
    BTN_HELP,
    BTN_MY_FILES,
    BTN_MY_SUB,
    BTN_TARIFFS,
    get_main_reply_kb,
    get_start_inline_kb,
)
from bot.middlewares.subscription_check import SubscriptionMiddleware
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService


class TestButtonsUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_buttons.db"
        self.db_service = DatabaseService(self.db_path)
        await self.db_service.init_db()

        self.settings = Settings(
            BOT_TOKEN="fake_token:123",
            ADMIN_IDS={999000},
            ALLOWED_TELEGRAM_IDS=set()
        )
        self.billing_service = BillingService(self.settings, self.db_service)
        self.middleware = SubscriptionMiddleware(self.settings, self.db_service, self.billing_service)
        self.storage = MemoryStorage()

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_keyboards_structure(self):
        """Проверка генерации постоянных клавиатур для пользователя и админа."""
        user_kb = get_main_reply_kb(is_admin=False)
        self.assertTrue(user_kb.is_persistent)
        self.assertTrue(user_kb.resize_keyboard)
        all_user_btn_texts = [btn.text for row in user_kb.keyboard for btn in row]
        self.assertIn(BTN_TARIFFS, all_user_btn_texts)
        self.assertIn(BTN_MY_SUB, all_user_btn_texts)
        self.assertIn(BTN_MY_FILES, all_user_btn_texts)
        self.assertIn(BTN_CLEAR_DB, all_user_btn_texts)
        self.assertIn(BTN_HELP, all_user_btn_texts)
        self.assertNotIn(BTN_ADMIN_CONSOLE, all_user_btn_texts)

        admin_kb = get_main_reply_kb(is_admin=True)
        all_admin_btn_texts = [btn.text for row in admin_kb.keyboard for btn in row]
        self.assertIn(BTN_ADMIN_CONSOLE, all_admin_btn_texts)
        self.assertIn(BTN_ADMIN_BILLING, all_admin_btn_texts)
        self.assertIn(BTN_ADMIN_STATS, all_admin_btn_texts)

        inline_kb = get_start_inline_kb(is_admin=True)
        inline_texts = [btn.text for row in inline_kb.inline_keyboard for btn in row]
        self.assertTrue(any("тариф" in t.lower() for t in inline_texts))
        self.assertTrue(any("админ" in t.lower() for t in inline_texts))

    async def test_subscription_middleware_permits_button_texts(self):
        """Middleware должен беспрепятственно пропускать нажатия на кнопки меню."""
        handler = AsyncMock(return_value="HANDLED")
        user = User(id=555, is_bot=False, first_name="UnsubscribedUser")

        # Проверяем каждую кнопку из меню
        for btn_text in ALL_MENU_BUTTONS:
            msg = MagicMock(spec=Message)
            msg.text = btn_text
            msg.successful_payment = None
            msg.answer = AsyncMock()
            data = {"event_from_user": user}

            res = await self.middleware(handler, msg, data)
            self.assertEqual(res, "HANDLED", f"Кнопка '{btn_text}' должна пропускаться middleware")
            handler.assert_awaited_once_with(msg, data)
            handler.reset_mock()

    async def test_cmd_start_sends_reply_and_inline_keyboards(self):
        """Проверка отправки Reply-меню и Inline-кнопок при команде /start."""
        msg = MagicMock(spec=Message)
        msg.from_user = User(id=999000, is_bot=False, first_name="AdminUser")
        msg.answer = AsyncMock()

        await cmd_start(msg, settings=self.settings)
        self.assertEqual(msg.answer.await_count, 2)

        # Первое сообщение должно содержать ReplyKeyboardMarkup
        first_call_kwargs = msg.answer.call_args_list[0][1]
        self.assertEqual(first_call_kwargs["reply_markup"].__class__.__name__, "ReplyKeyboardMarkup")

        # Второе сообщение должно содержать InlineKeyboardMarkup
        second_call_kwargs = msg.answer.call_args_list[1][1]
        self.assertEqual(second_call_kwargs["reply_markup"].__class__.__name__, "InlineKeyboardMarkup")

    async def test_admin_interactive_user_add_fsm(self):
        """Проверка интерактивного FSM добавления пользователя по кнопке."""
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = User(id=999000, is_bot=False, first_name="Admin")
        cb.message = MagicMock(spec=Message)
        cb.message.edit_text = AsyncMock()
        cb.answer = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.set_state = AsyncMock()
        state.clear = AsyncMock()

        # 1. Нажатие кнопки «➕ Добавить пользователя»
        await cb_admin_user_add_prompt(cb, state, self.settings)
        state.set_state.assert_awaited_once_with(AdminUserStates.waiting_for_user_id)
        cb.message.edit_text.assert_awaited_once()

        # 2. Ввод нечислового ID -> ошибка
        invalid_msg = MagicMock(spec=Message)
        invalid_msg.from_user = cb.from_user
        invalid_msg.text = "abc_invalid_id"
        invalid_msg.answer = AsyncMock()
        await handle_admin_user_id_input(invalid_msg, state, self.settings, self.db_service)
        invalid_msg.answer.assert_awaited_once()
        self.assertIn("должен быть числом", invalid_msg.answer.call_args[0][0])

        # 3. Ввод валидного ID -> успешное добавление
        valid_msg = MagicMock(spec=Message)
        valid_msg.from_user = cb.from_user
        valid_msg.text = "777888999"
        valid_msg.answer = AsyncMock()
        await handle_admin_user_id_input(valid_msg, state, self.settings, self.db_service)
        state.clear.assert_awaited_once()
        valid_msg.answer.assert_awaited_once()
        self.assertIn("успешно добавлен", valid_msg.answer.call_args[0][0])
        self.assertTrue(await self.db_service.is_user_allowed(777888999))

    async def test_admin_interactive_grant_sub_fsm(self):
        """Проверка интерактивного FSM выдачи подписки по кнопке."""
        cb = MagicMock(spec=CallbackQuery)
        cb.from_user = User(id=999000, is_bot=False, first_name="Admin")
        cb.message = MagicMock(spec=Message)
        cb.message.edit_text = AsyncMock()
        cb.answer = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.set_state = AsyncMock()
        state.clear = AsyncMock()

        # 1. Запуск FSM
        await cb_admin_billing_grant_prompt(cb, state, self.settings)
        state.set_state.assert_awaited_once_with(AdminBillingStates.waiting_for_grant_input)
        cb.message.edit_text.assert_awaited_once()

        # 2. Отправка корректных данных
        msg = MagicMock(spec=Message)
        msg.from_user = cb.from_user
        msg.text = "333444 45 pro"
        msg.answer = AsyncMock()
        bot_mock = AsyncMock()

        await handle_admin_grant_input(
            message=msg,
            state=state,
            bot=bot_mock,
            settings=self.settings,
            db_service=self.db_service,
            billing_service=self.billing_service
        )
        state.clear.assert_awaited_once()
        msg.answer.assert_awaited_once()
        self.assertIn("успешно выдана", msg.answer.call_args[0][0])

        sub = await self.db_service.get_active_subscription(333444)
        self.assertIsNotNone(sub)
        self.assertEqual(sub["tier"], "pro")
        self.assertEqual(sub["days_remaining"], 45)


if __name__ == "__main__":
    unittest.main()
