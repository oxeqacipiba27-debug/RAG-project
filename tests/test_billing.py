import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from bot.config import Settings
from bot.middlewares.subscription_check import SubscriptionMiddleware
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService


class TestBillingModule(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_billing.db"
        self.db_service = DatabaseService(self.db_path)
        await self.db_service.init_db()

        self.settings = Settings(
            BOT_TOKEN="fake_token:123",
            ADMIN_IDS={999999},
            ALLOWED_TELEGRAM_IDS=set(),
            PRICE_BASIC_FULL=3500,
            PRICE_BASIC_PROMO=2100,
            PRICE_PRO_FULL=5500,
            PRICE_PRO_PROMO=3300,
            EARLY_BIRD_LIMIT=10,
            SUBSCRIPTION_DAYS=30
        )
        self.billing_service = BillingService(self.settings, self.db_service)
        self.middleware = SubscriptionMiddleware(self.settings, self.db_service, self.billing_service)

    async def asyncTearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_early_bird_quota_and_pricing_transition(self):
        """Проверка динамического ценообразования: первые 10 со скидкой 40%, 11-й по полной цене."""
        # Начальное состояние: 10 мест со скидкой
        pricing = await self.billing_service.get_pricing_info(user_id=1001)
        self.assertTrue(pricing["is_promo"])
        self.assertEqual(pricing["basic_price"], 2100)
        self.assertEqual(pricing["pro_price"], 3300)
        self.assertEqual(pricing["remaining_slots"], 10)

        # Оформляем и подтверждаем оплату для первых 10 клиентов
        for i in range(1, 11):
            uid = 1000 + i
            pid = await self.db_service.create_payment(
                user_id=uid,
                amount=2100,
                tier="basic",
                payment_method="manual"
            )
            success, msg, sub_info = await self.db_service.confirm_payment(
                payment_id=pid,
                early_bird_limit=self.settings.EARLY_BIRD_LIMIT,
                subscription_days=30
            )
            self.assertTrue(success)
            self.assertTrue(sub_info["is_early_bird"], f"Клиент {i} должен получить Early-Bird статус")

            # Проверяем оставшиеся слоты
            slots_left = 10 - i
            p_info = await self.billing_service.get_pricing_info(user_id=9999)
            self.assertEqual(p_info["remaining_slots"], slots_left)

        # Проверяем для 11-го клиента: акция завершена
        pricing_11 = await self.billing_service.get_pricing_info(user_id=1011)
        self.assertFalse(pricing_11["is_promo"])
        self.assertEqual(pricing_11["basic_price"], 3500)
        self.assertEqual(pricing_11["pro_price"], 5500)
        self.assertEqual(pricing_11["remaining_slots"], 0)

        # 11-й клиент оплачивает полную цену
        pid_11 = await self.db_service.create_payment(
            user_id=1011,
            amount=3500,
            tier="basic",
            payment_method="manual"
        )
        success_11, _, sub_info_11 = await self.db_service.confirm_payment(
            payment_id=pid_11,
            early_bird_limit=self.settings.EARLY_BIRD_LIMIT,
            subscription_days=30
        )
        self.assertTrue(success_11)
        self.assertFalse(sub_info_11["is_early_bird"], "11-й клиент не должен получить Early-Bird")

    async def test_atomic_payment_confirmation_and_double_approval(self):
        """Проверка защиты от повторного подтверждения чека (идемпотентность и race condition)."""
        pid = await self.db_service.create_payment(
            user_id=2001,
            amount=2100,
            tier="basic",
            payment_method="manual"
        )

        # Первое подтверждение
        ok1, msg1, sub1 = await self.db_service.confirm_payment(pid)
        self.assertTrue(ok1)
        self.assertIsNotNone(sub1)

        # Повторное подтверждение тем же или другим админом
        ok2, msg2, sub2 = await self.db_service.confirm_payment(pid)
        self.assertFalse(ok2)
        self.assertIn("уже обработан", msg2)

        # Попытка отклонить уже подтвержденный платеж
        ok_rej, msg_rej, _ = await self.db_service.reject_payment(pid)
        self.assertFalse(ok_rej)
        self.assertIn("уже обработан", msg_rej)

    async def test_subscription_lifecycle_and_extension(self):
        """Проверка расчета 30 дней и корректного продления подписки."""
        user_id = 3001
        pid1 = await self.db_service.create_payment(user_id=user_id, amount=2100, tier="basic")
        ok1, _, sub1 = await self.db_service.confirm_payment(pid1, subscription_days=30)
        self.assertTrue(ok1)

        # Проверяем активную подписку
        active_sub = await self.db_service.get_active_subscription(user_id)
        self.assertIsNotNone(active_sub)
        self.assertEqual(active_sub["days_remaining"], 30)

        # Продлеваем еще на 30 дней
        pid2 = await self.db_service.create_payment(user_id=user_id, amount=2100, tier="basic")
        ok2, _, sub2 = await self.db_service.confirm_payment(pid2, subscription_days=30)
        self.assertTrue(ok2)

        # Срок должен быть суммарно около 60 дней
        extended_sub = await self.db_service.get_active_subscription(user_id)
        self.assertGreaterEqual(extended_sub["days_remaining"], 59)
        self.assertLessEqual(extended_sub["days_remaining"], 60)

    async def test_grant_sub_admin_command(self):
        """Проверка ручной выдачи подписки администратором."""
        target_user = 4001
        # До выдачи
        self.assertFalse(await self.billing_service.has_active_access(target_user))

        # Выдача на 15 дней
        granted = await self.db_service.grant_subscription(
            user_id=target_user,
            days=15,
            tier="pro",
            is_early_bird=0,
            admin_id=999999
        )
        self.assertEqual(granted["tier"], "pro")
        self.assertEqual(granted["days"], 15)

        # После выдачи доступ есть
        self.assertTrue(await self.billing_service.has_active_access(target_user))
        sub = await self.db_service.get_active_subscription(target_user)
        self.assertEqual(sub["tier"], "pro")
        self.assertEqual(sub["days_remaining"], 15)

    async def test_subscription_middleware_access_control(self):
        """Проверка SubscriptionMiddleware: блокировка без подписки, доступ по подписке и байпас админа."""
        handler = AsyncMock(return_value="RAG_SUCCESS")

        # 1. Системный админ: свободный доступ ко всему
        admin_user = User(id=999999, is_bot=False, first_name="Admin")
        admin_msg = MagicMock(spec=Message)
        admin_msg.text = "Как работает система?"
        data_admin = {"event_from_user": admin_user}

        res_admin = await self.middleware(handler, admin_msg, data_admin)
        self.assertEqual(res_admin, "RAG_SUCCESS")

        # 2. Обычный пользователь без подписки: открытые команды разрешены
        regular_user = User(id=5001, is_bot=False, first_name="Guest")
        start_msg = MagicMock(spec=Message)
        start_msg.text = "/start"
        data_guest = {"event_from_user": regular_user}

        res_start = await self.middleware(handler, start_msg, data_guest)
        self.assertEqual(res_start, "RAG_SUCCESS")

        sub_msg = MagicMock(spec=Message)
        sub_msg.text = "/subscribe"
        res_sub = await self.middleware(handler, sub_msg, data_guest)
        self.assertEqual(res_sub, "RAG_SUCCESS")

        # 3. Обычный пользователь без подписки: текстовый вопрос к RAG блокируется
        rag_query_msg = MagicMock(spec=Message)
        rag_query_msg.text = "Расскажи мне содержание документа"
        rag_query_msg.successful_payment = None
        rag_query_msg.answer = AsyncMock()

        res_blocked = await self.middleware(handler, rag_query_msg, data_guest)
        self.assertIsNone(res_blocked)
        rag_query_msg.answer.assert_awaited_once()
        self.assertIn("активная подписка", rag_query_msg.answer.call_args[0][0])

        # 4. Оформляем подписку для пользователя
        await self.db_service.grant_subscription(user_id=5001, days=30, tier="basic")

        # Теперь вопрос к RAG разрешен
        res_allowed = await self.middleware(handler, rag_query_msg, data_guest)
        self.assertEqual(res_allowed, "RAG_SUCCESS")


if __name__ == "__main__":
    unittest.main()
