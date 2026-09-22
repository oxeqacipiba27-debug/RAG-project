from aiogram import Bot, F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger

from bot.config import Settings
from bot.keyboards.billing_kb import get_admin_billing_kb, get_cancel_billing_fsm_kb
from bot.keyboards.menu_kb import BTN_ADMIN_BILLING
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService

router = Router(name="admin_billing_router")


class AdminBillingStates(StatesGroup):
    waiting_for_grant_input = State()



def is_admin(user_id: int, settings: Settings) -> bool:
    """Проверка прав системного администратора."""
    return user_id in settings.ADMIN_IDS


def format_billing_dashboard(stats: dict) -> str:
    """Форматирование сводной панели биллинга для администратора."""
    rev_str = f"{stats['total_revenue']:,} ₽".replace(",", " ")
    pending_alert = f" (⚠️ <b>{stats['pending_payments_count']}</b> требуют внимания!)" if stats['pending_payments_count'] > 0 else " (все проверены)"

    return (
        "💰 <b>Панель управления биллингом SchoolX RAG</b>\n\n"
        "👥 <b>Подписки:</b>\n"
        f"• Активных подписчиков: <b>{stats['active_subscriptions']}</b>\n"
        f"• Всего создано подписок: <b>{stats['total_subscriptions']}</b>\n"
        f"• Активных Early-Bird: <b>{stats['early_bird_count']} из {stats['early_bird_limit']}</b>\n"
        f"• Осталось мест со скидкой 40%: <b>{stats['early_bird_remaining']}</b>\n\n"
        "💳 <b>Финансы и чеки:</b>\n"
        f"• Подтвержденная выручка: <b>{rev_str}</b>\n"
        f"• Ожидающих проверки чеков: <b>{stats['pending_payments_count']}</b>{pending_alert}\n\n"
        "🛠 <b>Ручное управление подпиской:</b>\n"
        "<code>/grant_sub &lt;user_id&gt; &lt;days&gt; &lt;tier&gt;</code>\n"
        "<i>Пример: <code>/grant_sub 123456789 30 pro</code></i>"
    )


# ==============================================================================
# Команды админ-биллинга
# ==============================================================================

@router.message(or_f(Command("admin_billing"), F.text.in_([BTN_ADMIN_BILLING, "Биллинг", "Чеки", "Подписки"])))
async def cmd_admin_billing(
    message: Message,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Команда /admin_billing: просмотр сводки биллинга."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    stats = await db_service.get_billing_stats(early_bird_limit=settings.EARLY_BIRD_LIMIT)
    text = format_billing_dashboard(stats)
    await message.answer(text, reply_markup=get_admin_billing_kb())


@router.message(Command("grant_sub"))
async def cmd_grant_sub(
    message: Message,
    bot: Bot,
    settings: Settings,
    db_service: DatabaseService,
    billing_service: BillingService
) -> None:
    """Команда ручной выдачи подписки: /grant_sub <user_id> <days> <tier>."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    admin_id = message.from_user.id
    args = (message.text or "").strip().split()
    if len(args) < 3:
        await message.answer(
            "ℹ️ <b>Формат команды:</b>\n"
            "<code>/grant_sub &lt;user_id&gt; &lt;days&gt; [basic|pro]</code>\n\n"
            "<b>Примеры:</b>\n"
            "• <code>/grant_sub 123456789 30 pro</code> — выдать тариф «ПРО» на 30 дней\n"
            "• <code>/grant_sub 123456789 14 basic</code> — выдать тариф «Базовый» на 14 дней"
        )
        return

    user_str = args[1]
    days_str = args[2]
    tier = args[3].lower() if len(args) > 3 else "basic"
    if tier not in {"basic", "pro"}:
        tier = "basic"

    if not (user_str.isdigit() or (user_str.startswith("-") and user_str[1:].isdigit())):
        await message.answer("❌ <b>Ошибка:</b> Telegram ID должен быть числом.")
        return

    if not days_str.isdigit() or int(days_str) <= 0:
        await message.answer("❌ <b>Ошибка:</b> Количество дней должно быть положительным числом.")
        return

    target_id = int(user_str)
    days = int(days_str)
    tier_name = billing_service.get_tier_name(tier)

    sub_info = await db_service.grant_subscription(
        user_id=target_id,
        days=days,
        tier=tier,
        is_early_bird=0,
        admin_id=admin_id
    )

    await message.answer(
        f"✅ <b>Подписка успешно выдана!</b>\n\n"
        f"• Пользователь: <code>{target_id}</code>\n"
        f"• Тариф: <b>{tier_name}</b>\n"
        f"• Срок: <b>{days} дней</b>\n"
        f"• Действует до: <code>{sub_info['expires_at']}</code>"
    )

    # Уведомление пользователю
    try:
        await bot.send_message(
            chat_id=target_id,
            text=(
                f"🎁 <b>Вам предоставлена подписка!</b>\n\n"
                f"Администратор активировал для вас тариф <b>{tier_name}</b> на <b>{days} дней</b>.\n"
                f"• Действует до: <code>{sub_info['expires_at']}</code>\n\n"
                "Теперь вы можете загружать файлы и задавать любые вопросы нейросети. "
                "Добро пожаловать в SchoolX RAG!"
            )
        )
        logger.info(f"Notified user {target_id} about granted subscription.")
    except Exception as e:
        logger.warning(f"Could not notify user {target_id} about granted sub: {e}")


# ==============================================================================
# Callback-обработчики админ-биллинга
# ==============================================================================

@router.callback_query(F.data == "admin:billing:refresh")
async def cb_admin_billing_refresh(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Обновление дашборда биллинга."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    stats = await db_service.get_billing_stats(early_bird_limit=settings.EARLY_BIRD_LIMIT)
    text = format_billing_dashboard(stats)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_admin_billing_kb())
    await callback.answer("Сводка обновлена.")


@router.callback_query(F.data == "admin:billing:grant_prompt")
async def cb_admin_billing_grant_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings
) -> None:
    """Запуск интерактивного диалога выдачи подписки."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    await state.set_state(AdminBillingStates.waiting_for_grant_input)
    text = (
        "🎁 <b>Ручная выдача подписки</b>\n\n"
        "Отправьте данные в формате:\n"
        "<code>&lt;telegram_id&gt; &lt;кол-во дней&gt; [basic|pro]</code>\n\n"
        "<b>Примеры:</b>\n"
        "• <code>123456789 30 pro</code> — тариф ПРО на 30 дней\n"
        "• <code>123456789 14 basic</code> — тариф Базовый на 14 дней\n\n"
        "<i>Для отмены нажмите кнопку ниже:</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_cancel_billing_fsm_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:billing:cancel_fsm")
async def cb_admin_billing_cancel_fsm(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Отмена FSM-ввода в панели биллинга."""
    await state.clear()
    stats = await db_service.get_billing_stats(early_bird_limit=settings.EARLY_BIRD_LIMIT)
    text = format_billing_dashboard(stats)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_admin_billing_kb())
    await callback.answer("Ввод отменен.")


@router.message(AdminBillingStates.waiting_for_grant_input)
async def handle_admin_grant_input(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
    db_service: DatabaseService,
    billing_service: BillingService
) -> None:
    """Обработка параметров выдачи подписки."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    admin_id = message.from_user.id
    raw = (message.text or "").strip().split()
    if len(raw) < 2:
        await message.answer(
            "⚠️ <b>Неверный формат.</b>\n"
            "Укажите Telegram ID и количество дней через пробел:\n"
            "<code>&lt;telegram_id&gt; &lt;дней&gt; [basic|pro]</code>\n"
            "Пример: <code>123456789 30 pro</code>",
            reply_markup=get_cancel_billing_fsm_kb()
        )
        return

    user_str = raw[0]
    days_str = raw[1]
    tier = raw[2].lower() if len(raw) > 2 else "basic"
    if tier not in {"basic", "pro"}:
        tier = "basic"

    if not (user_str.isdigit() or (user_str.startswith("-") and user_str[1:].isdigit())):
        await message.answer(
            "❌ <b>Ошибка:</b> Telegram ID должен быть числом.",
            reply_markup=get_cancel_billing_fsm_kb()
        )
        return

    if not days_str.isdigit() or int(days_str) <= 0:
        await message.answer(
            "❌ <b>Ошибка:</b> Количество дней должно быть положительным числом.",
            reply_markup=get_cancel_billing_fsm_kb()
        )
        return

    target_id = int(user_str)
    days = int(days_str)
    tier_name = billing_service.get_tier_name(tier)

    sub_info = await db_service.grant_subscription(
        user_id=target_id,
        days=days,
        tier=tier,
        is_early_bird=0,
        admin_id=admin_id
    )
    await state.clear()

    await message.answer(
        f"✅ <b>Подписка успешно выдана!</b>\n\n"
        f"• Пользователь: <code>{target_id}</code>\n"
        f"• Тариф: <b>{tier_name}</b>\n"
        f"• Срок: <b>{days} дней</b>\n"
        f"• Действует до: <code>{sub_info['expires_at']}</code>"
    )

    try:
        await bot.send_message(
            chat_id=target_id,
            text=(
                f"🎁 <b>Вам предоставлена подписка!</b>\n\n"
                f"Администратор активировал для вас тариф <b>{tier_name}</b> на <b>{days} дней</b>.\n"
                f"• Действует до: <code>{sub_info['expires_at']}</code>\n\n"
                "Теперь вы можете загружать файлы и задавать любые вопросы нейросети. "
                "Добро пожаловать в SchoolX RAG!"
            )
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_id}: {e}")


@router.callback_query(F.data == "admin:billing:pending")
async def cb_admin_billing_pending(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService,
    billing_service: BillingService
) -> None:
    """Список платежей, ожидающих проверки чека."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    pending = await db_service.get_pending_payments(limit=10)
    if not pending:
        await callback.answer("Нет ожидающих платежей.", show_alert=True)
        return

    lines = ["📋 <b>Платежи, ожидающие подтверждения:</b>\n"]
    buttons = []

    for p in pending:
        tier_name = billing_service.get_tier_name(p["tier"])
        amount_str = f"{p['amount']:,} ₽".replace(",", " ")
        lines.append(
            f"• <code>{p['id']}</code>: User <code>{p['user_id']}</code> | "
            f"<b>{tier_name}</b> | <b>{amount_str}</b> ({p['created_at']})"
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"✅ Подтвердить {p['id']} ({amount_str})",
                callback_data=f"admin:pay_confirm:{p['id']}"
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"admin:pay_reject:{p['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="◀️ Назад к сводке", callback_data="admin:billing:refresh")
    ])

    text = "\n".join(lines)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:pay_confirm:"))
async def cb_admin_pay_confirm(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    db_service: DatabaseService,
    billing_service: BillingService
) -> None:
    """Подтверждение оплаты администратором."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    payment_id = callback.data.split(":")[2]
    admin = callback.from_user
    admin_name = f"@{admin.username}" if admin.username else f"ID {admin.id}"

    success, msg, sub_info = await db_service.confirm_payment(
        payment_id=payment_id,
        admin_id=admin.id,
        early_bird_limit=settings.EARLY_BIRD_LIMIT,
        subscription_days=settings.SUBSCRIPTION_DAYS
    )

    if not success:
        await callback.answer(msg, show_alert=True)
        return

    await callback.answer("Платеж подтвержден!", show_alert=False)

    tier_name = billing_service.get_tier_name(sub_info["tier"])
    amount_str = f"{sub_info['amount']:,} ₽".replace(",", " ")
    promo_badge = "🔥 Early-Bird (скидка 40%)" if sub_info.get("is_early_bird") else "Стандартная цена"

    # Обновление сообщения у админа
    update_text = (
        f"✅ <b>ОПЛАТА ПОДТВЕРЖДЕНА</b> администратором {admin_name}\n\n"
        f"• ID транзакции: <code>{payment_id}</code>\n"
        f"• Пользователь: <code>{sub_info['user_id']}</code>\n"
        f"• Тариф: <b>{tier_name}</b> ({promo_badge})\n"
        f"• Сумма: <b>{amount_str}</b>\n"
        f"• Подписка активна до: <code>{sub_info['expires_at']}</code>"
    )

    if callback.message and isinstance(callback.message, Message):
        try:
            if callback.message.caption:
                await callback.message.edit_caption(caption=update_text, reply_markup=None)
            else:
                await callback.message.edit_text(text=update_text, reply_markup=None)
        except Exception as e:
            logger.debug(f"Failed to update admin message markup: {e}")

    # Уведомление пользователю
    user_id = sub_info["user_id"]
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Ваша оплата успешно подтверждена!</b>\n\n"
                f"Подписка по тарифу <b>{tier_name}</b> активирована на <b>30 дней</b>.\n"
                f"• Статус: <b>🟢 Активна</b> ({promo_badge})\n"
                f"• Действует до: <code>{sub_info['expires_at']}</code>\n\n"
                "🚀 Теперь вам доступен полный функционал: загружайте файлы "
                "и задавайте любые вопросы вашей персональной базе знаний!"
            )
        )
        logger.info(f"User {user_id} notified about successful payment {payment_id}.")
    except Exception as e:
        logger.error(f"Failed to notify user {user_id} about confirmed payment: {e}")


@router.callback_query(F.data.startswith("admin:pay_reject:"))
async def cb_admin_pay_reject(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    db_service: DatabaseService,
    billing_service: BillingService
) -> None:
    """Отклонение оплаты администратором."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    payment_id = callback.data.split(":")[2]
    admin = callback.from_user
    admin_name = f"@{admin.username}" if admin.username else f"ID {admin.id}"

    success, msg, payment = await db_service.reject_payment(
        payment_id=payment_id,
        admin_id=admin.id
    )

    if not success:
        await callback.answer(msg, show_alert=True)
        return

    await callback.answer("Платеж отклонен.", show_alert=False)

    tier_name = billing_service.get_tier_name(payment["tier"])
    amount_str = f"{payment['amount']:,} ₽".replace(",", " ")

    update_text = (
        f"❌ <b>ОПЛАТА ОТКЛОНЕНА</b> администратором {admin_name}\n\n"
        f"• ID транзакции: <code>{payment_id}</code>\n"
        f"• Пользователь: <code>{payment['user_id']}</code>\n"
        f"• Тариф: <b>{tier_name}</b>\n"
        f"• Сумма: <b>{amount_str}</b>"
    )

    if callback.message and isinstance(callback.message, Message):
        try:
            if callback.message.caption:
                await callback.message.edit_caption(caption=update_text, reply_markup=None)
            else:
                await callback.message.edit_text(text=update_text, reply_markup=None)
        except Exception as e:
            logger.debug(f"Failed to update admin message markup: {e}")

    # Уведомление пользователю
    user_id = payment["user_id"]
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "⚠️ <b>Ваш платеж отклонен администратором.</b>\n\n"
                "Квитанция не прошла проверку или средства не поступили на счет.\n"
                "Если произошла ошибка или вы хотите повторить оплату, "
                "пожалуйста, выберите тариф заново: /subscribe"
            )
        )
        logger.info(f"User {user_id} notified about rejected payment {payment_id}.")
    except Exception as e:
        logger.error(f"Failed to notify user {user_id} about rejected payment: {e}")
