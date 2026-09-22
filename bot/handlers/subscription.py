from aiogram import Bot, F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ContentType,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from loguru import logger

from bot.config import Settings
from bot.keyboards.billing_kb import (
    get_admin_payment_approval_kb,
    get_my_sub_kb,
    get_receipt_upload_kb,
    get_tariffs_kb,
    get_tier_checkout_kb,
)
from bot.keyboards.menu_kb import BTN_MY_SUB, BTN_TARIFFS
from bot.services.billing_service import BillingService
from bot.services.db_service import DatabaseService

router = Router(name="subscription_router")


class BillingStates(StatesGroup):
    waiting_for_receipt = State()


def format_tariffs_text(pricing: dict) -> str:
    """Форматирование экрана витрины тарифов с динамическим счетчиком Early-Bird."""
    basic_price = f"{pricing['basic_price']:,} ₽".replace(",", " ")
    pro_price = f"{pricing['pro_price']:,} ₽".replace(",", " ")
    basic_full = f"{pricing['basic_full']:,} ₽".replace(",", " ")
    pro_full = f"{pricing['pro_full']:,} ₽".replace(",", " ")

    if pricing["is_promo"]:
        if pricing.get("is_renewal"):
            promo_header = (
                "🎁 <b>Для вас действует специальная цена продления (скидка 40%)!</b>\n\n"
            )
        else:
            remaining = pricing["remaining_slots"]
            promo_header = (
                f"🔥 <b>Квота Early-Bird: Скидка 40% для первых 10 клиентов!</b>\n"
                f"Осталось акционных мест: <b>{remaining} из {pricing['early_limit']}</b>\n\n"
            )
    else:
        promo_header = (
            "⚠️ <i>Все 10 акционных мест со скидкой 40% заняты. "
            "Действует стандартная стоимость тарифов:</i>\n\n"
        )

    return (
        "💎 <b>Тарифные планы SchoolX RAG</b>\n\n"
        "Персональная изолированная база знаний с подключением к мощной локальной нейросети.\n\n"
        f"{promo_header}"
        "📦 <b>Тариф «Базовый»:</b>\n"
        "• Персональная изолированная база документов\n"
        "• Загрузка файлов до 10 документов (PDF, Word, Excel, PowerPoint, TXT)\n"
        "• Быстрые ответы от локальной LLM с ссылками на источники\n"
        f"💳 Стоимость: <b>{basic_price}/мес</b>"
        + (f" <s>({basic_full})</s>" if pricing["is_promo"] else "")
        + "\n\n"
        "🚀 <b>Тариф «ПРО»:</b>\n"
        "• Все возможности тарифа «Базовый»\n"
        "• Расширенное хранилище (до 50 документов)\n"
        "• Приоритетная обработка в очереди инференса\n"
        "• Индивидуальная настройка температуры и длины ответов\n"
        f"💳 Стоимость: <b>{pro_price}/мес</b>"
        + (f" <s>({pro_full})</s>" if pricing["is_promo"] else "")
        + "\n\n"
        "<i>Срок действия любой подписки: <b>30 дней</b>. "
        "Выберите тариф ниже для оформления:</i>"
    )


# ==============================================================================
# Команды подписки
# ==============================================================================

@router.message(or_f(Command("subscribe"), F.text.in_([BTN_TARIFFS, "Тарифы", "Подписка"])))
async def cmd_subscribe(
    message: Message,
    billing_service: BillingService
) -> None:
    """Команда /subscribe и кнопка «Тарифы»: витрина тарифов."""
    user_id = message.from_user.id if message.from_user else 0
    pricing = await billing_service.get_pricing_info(user_id=user_id)
    text = format_tariffs_text(pricing)
    await message.answer(
        text,
        reply_markup=get_tariffs_kb(
            basic_price=pricing["basic_price"],
            pro_price=pricing["pro_price"],
            is_promo=pricing["is_promo"],
            remaining_slots=pricing["remaining_slots"]
        )
    )


@router.message(or_f(Command("my_sub"), F.text.in_([BTN_MY_SUB, "Моя подписка", "Мой статус"])))
async def cmd_my_sub(
    message: Message,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Команда /my_sub: информация о текущей подписке пользователя."""
    user_id = message.from_user.id if message.from_user else 0

    if user_id in settings.ADMIN_IDS:
        text = (
            "👑 <b>Статус: Администратор системы</b>\n\n"
            "Вам предоставлен <b>пожизненный неограниченный доступ</b> "
            "ко всем функциям бота и RAG-системе."
        )
        await message.answer(text, reply_markup=get_my_sub_kb(has_sub=True))
        return

    sub = await db_service.get_active_subscription(user_id)
    if not sub:
        text = (
            "ℹ️ <b>У вас нет активной подписки.</b>\n\n"
            "Для загрузки личных документов и ответов нейросети оформите подписку. "
            "Нажмите кнопку ниже, чтобы выбрать подходящий тариф:"
        )
        await message.answer(text, reply_markup=get_my_sub_kb(has_sub=False))
        return

    tier_title = "«ПРО» 🚀" if sub["tier"] == "pro" else "«Базовый» 📦"
    promo_badge = "🔥 Early-Bird (скидка 40%)" if sub.get("is_early_bird") else "Стандартный"

    text = (
        "💎 <b>Ваша подписка:</b>\n\n"
        f"• Тариф: <b>{tier_title}</b>\n"
        f"• Тип: <b>{promo_badge}</b>\n"
        f"• Статус: <b>🟢 Активна</b>\n"
        f"• Дата начала: <code>{sub['starts_at']}</code>\n"
        f"• Действует до: <code>{sub['expires_at']}</code>\n"
        f"• Осталось дней: <b>{sub['days_remaining']}</b>\n\n"
        "<i>Вы можете продлить подписку в любое время: новые 30 дней "
        "будут прибавлены к текущему сроку окончания.</i>"
    )
    await message.answer(text, reply_markup=get_my_sub_kb(has_sub=True))


# ==============================================================================
# Callback-обработчики выбора тарифов
# ==============================================================================

@router.callback_query(F.data == "sub:tariffs")
async def cb_sub_tariffs(
    callback: CallbackQuery,
    billing_service: BillingService
) -> None:
    """Возврат к витрине тарифов."""
    user_id = callback.from_user.id if callback.from_user else 0
    pricing = await billing_service.get_pricing_info(user_id=user_id)
    text = format_tariffs_text(pricing)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=get_tariffs_kb(
                basic_price=pricing["basic_price"],
                pro_price=pricing["pro_price"],
                is_promo=pricing["is_promo"],
                remaining_slots=pricing["remaining_slots"]
            )
        )
    await callback.answer()


@router.callback_query(F.data == "sub:my_sub")
async def cb_sub_my_sub(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Просмотр статуса подписки через Callback."""
    user_id = callback.from_user.id if callback.from_user else 0

    if user_id in settings.ADMIN_IDS:
        text = (
            "👑 <b>Статус: Администратор системы</b>\n\n"
            "Вам предоставлен <b>пожизненный неограниченный доступ</b> "
            "ко всем функциям бота и RAG-системе."
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(text, reply_markup=get_my_sub_kb(has_sub=True))
        await callback.answer()
        return

    sub = await db_service.get_active_subscription(user_id)
    if not sub:
        text = (
            "ℹ️ <b>У вас нет активной подписки.</b>\n\n"
            "Для загрузки личных документов и ответов нейросети оформите подписку:"
        )
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text(text, reply_markup=get_my_sub_kb(has_sub=False))
        await callback.answer()
        return

    tier_title = "«ПРО» 🚀" if sub["tier"] == "pro" else "«Базовый» 📦"
    promo_badge = "🔥 Early-Bird (скидка 40%)" if sub.get("is_early_bird") else "Стандартный"

    text = (
        "💎 <b>Ваша подписка:</b>\n\n"
        f"• Тариф: <b>{tier_title}</b>\n"
        f"• Тип: <b>{promo_badge}</b>\n"
        f"• Статус: <b>🟢 Активна</b>\n"
        f"• Дата начала: <code>{sub['starts_at']}</code>\n"
        f"• Действует до: <code>{sub['expires_at']}</code>\n"
        f"• Осталось дней: <b>{sub['days_remaining']}</b>\n\n"
        "<i>Вы можете продлить подписку в любое время: новые 30 дней "
        "будут прибавлены к текущему сроку.</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_my_sub_kb(has_sub=True))
    await callback.answer()


@router.callback_query(F.data.startswith("sub:select:"))
async def cb_sub_select_tier(
    callback: CallbackQuery,
    settings: Settings,
    billing_service: BillingService
) -> None:
    """Выбор тарифа и переход к оплате."""
    tier = callback.data.split(":")[2]
    user_id = callback.from_user.id if callback.from_user else 0

    price = await billing_service.get_tier_price(tier, user_id=user_id)
    tier_name = billing_service.get_tier_name(tier)
    price_str = f"{price:,} ₽".replace(",", " ")

    if tier == "pro":
        features = (
            "• До 50 загруженных документов\n"
            "• Приоритет в очереди генерации LM Studio\n"
            "• Персональная настройка параметров ответа"
        )
    else:
        features = (
            "• До 10 загруженных документов\n"
            "• Быстрые ответы по вашим файлам\n"
            "• Изолированная база знаний"
        )

    text = (
        f"💳 <b>Оформление подписки: Тариф {tier_name}</b>\n\n"
        f"<b>В тариф входит:</b>\n{features}\n\n"
        f"• Срок действия: <b>30 дней</b>\n"
        f"• К оплате: <b>{price_str}</b>\n\n"
        "Нажмите кнопку ниже, чтобы перейти к оплате:"
    )

    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=get_tier_checkout_kb(tier, price, settings.PAYMENT_MODE)
        )
    await callback.answer()


# ==============================================================================
# Оплата по СБП / Карте (Ручной режим)
# ==============================================================================

@router.callback_query(F.data.startswith("sub:pay_manual:"))
async def cb_sub_pay_manual(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    billing_service: BillingService
) -> None:
    """Показ реквизитов для ручного перевода и переход в состояние ожидания чека."""
    parts = callback.data.split(":")
    tier = parts[2]
    price = int(parts[3])
    tier_name = billing_service.get_tier_name(tier)
    price_str = f"{price:,} ₽".replace(",", " ")

    # Сохранение данных заказа в FSM
    await state.set_state(BillingStates.waiting_for_receipt)
    await state.update_data(tier=tier, price=price)

    text = (
        f"🧾 <b>Оплата тарифа {tier_name} ({price_str})</b>\n\n"
        f"{settings.MANUAL_PAYMENT_DETAILS}\n\n"
        f"<b>Сумма к переводу:</b> <code>{price}</code> ₽\n\n"
        "📸 <b>После совершения перевода:</b>\n"
        "Отправьте в этот чат <b>фотографию, скриншот или PDF-файл квитанции (чека)</b> об оплате."
    )

    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_receipt_upload_kb())
    await callback.answer()


@router.callback_query(F.data == "sub:cancel_upload")
async def cb_sub_cancel_upload(
    callback: CallbackQuery,
    state: FSMContext,
    billing_service: BillingService
) -> None:
    """Отмена отправки чека."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    pricing = await billing_service.get_pricing_info(user_id=user_id)
    text = (
        "❌ <i>Отправка чека отменена.</i>\n\n"
        + format_tariffs_text(pricing)
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=get_tariffs_kb(
                basic_price=pricing["basic_price"],
                pro_price=pricing["pro_price"],
                is_promo=pricing["is_promo"],
                remaining_slots=pricing["remaining_slots"]
            )
        )
    await callback.answer("Отменено.")


@router.message(BillingStates.waiting_for_receipt, F.photo | F.document)
async def handle_receipt_upload(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
    db_service: DatabaseService,
    billing_service: BillingService
) -> None:
    """Прием квитанции об оплате (фото или документ) и пересылка администраторам."""
    data = await state.get_data()
    tier = data.get("tier", "basic")
    price = data.get("price", 0)
    tier_name = billing_service.get_tier_name(tier)
    user = message.from_user
    user_id = user.id if user else 0
    username = f"@{user.username}" if user and user.username else "нет username"

    # Извлечение file_id
    if message.photo:
        file_id = message.photo[-1].file_id
        is_photo = True
    else:
        file_id = message.document.file_id
        is_photo = False

    # Регистрация платежа в БД со статусом pending
    payment_id = await db_service.create_payment(
        user_id=user_id,
        amount=price,
        tier=tier,
        payment_method="manual",
        receipt_file_id=file_id
    )

    # Сброс FSM
    await state.clear()

    # Ответ пользователю
    await message.answer(
        "✅ <b>Квитанция успешно принята на проверку!</b>\n\n"
        f"• Номер платежа: <code>{payment_id}</code>\n"
        f"• Выбранный тариф: <b>{tier_name}</b>\n"
        f"• Сумма: <b>{price:,} ₽</b>\n\n"
        "⏳ Администратор проверит поступление средств и активирует доступ. "
        "Обычно это занимает несколько минут. Мы сразу пришлем вам уведомление!"
        .replace(",", " ")
    )

    # Пересылка чека всем администраторам
    admin_caption = (
        "🔔 <b>Новый платеж на модерации!</b>\n\n"
        f"• Пользователь: {username} (ID: <code>{user_id}</code>)\n"
        f"• Тариф: <b>{tier_name}</b>\n"
        f"• Сумма: <b>{price:,} ₽</b>\n"
        f"• ID транзакции: <code>{payment_id}</code>\n\n"
        "Проверьте поступление перевода на банковский счет и выберите действие:"
        .replace(",", " ")
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            if is_photo:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=file_id,
                    caption=admin_caption,
                    reply_markup=get_admin_payment_approval_kb(payment_id)
                )
            else:
                await bot.send_document(
                    chat_id=admin_id,
                    document=file_id,
                    caption=admin_caption,
                    reply_markup=get_admin_payment_approval_kb(payment_id)
                )
            logger.info(f"Forwarded receipt for payment {payment_id} to admin {admin_id}.")
        except Exception as e:
            logger.error(f"Failed to forward payment receipt to admin {admin_id}: {e}")


@router.message(BillingStates.waiting_for_receipt)
async def handle_invalid_receipt(message: Message) -> None:
    """Обработка текстового сообщения в режиме ожидания чека."""
    await message.answer(
        "⚠️ <b>Пожалуйста, отправьте чек об оплате.</b>\n\n"
        "Прикрепите фотографию, скриншот или документ (PDF/PNG) к сообщению.\n"
        "Если вы передумали, нажмите кнопку отмены ниже:",
        reply_markup=get_receipt_upload_kb()
    )


# ==============================================================================
# Telegram Payments (Онлайн-оплата при наличии токена провайдера)
# ==============================================================================

@router.callback_query(F.data.startswith("sub:pay_online:"))
async def cb_sub_pay_online(
    callback: CallbackQuery,
    bot: Bot,
    settings: Settings,
    billing_service: BillingService
) -> None:
    """Формирование нативного инвойса Telegram Payments."""
    if not settings.PAYMENT_PROVIDER_TOKEN:
        await callback.answer(
            "Онлайн-оплата временно недоступна. Пожалуйста, используйте перевод по СБП.",
            show_alert=True
        )
        return

    parts = callback.data.split(":")
    tier = parts[2]
    price = int(parts[3])
    tier_name = billing_service.get_tier_name(tier)
    user_id = callback.from_user.id if callback.from_user else 0

    prices = [LabeledPrice(label=f"Подписка {tier_name} (30 дней)", amount=price * 100)]

    try:
        await bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Подписка SchoolX RAG — {tier_name}",
            description=f"Доступ к персональной RAG-системе на 30 дней по тарифу {tier_name}.",
            payload=f"online_pay:{user_id}:{tier}:{price}",
            provider_token=settings.PAYMENT_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
            start_parameter=f"sub_{tier}"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Error creating Telegram Payments invoice: {e}")
        await callback.answer("Ошибка при создании счета на оплату.", show_alert=True)


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery, bot: Bot) -> None:
    """Подтверждение готовности принять платеж Telegram Payments."""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(
    message: Message,
    db_service: DatabaseService,
    settings: Settings,
    billing_service: BillingService
) -> None:
    """Обработка успешной оплаты через Telegram Payments."""
    sp = message.successful_payment
    payload = sp.invoice_payload
    # payload format: "online_pay:<user_id>:<tier>:<price>"
    parts = payload.split(":")
    if len(parts) >= 4:
        tier = parts[2]
        amount = int(parts[3])
    else:
        tier = "basic"
        amount = sp.total_amount // 100

    user_id = message.from_user.id if message.from_user else 0
    telegram_charge_id = sp.telegram_payment_charge_id

    # Создаем и сразу подтверждаем платеж
    payment_id = await db_service.create_payment(
        user_id=user_id,
        amount=amount,
        tier=tier,
        payment_method="telegram_payments",
        payment_id=f"tg_{telegram_charge_id[:10]}"
    )

    success, msg, sub_info = await db_service.confirm_payment(
        payment_id=payment_id,
        early_bird_limit=settings.EARLY_BIRD_LIMIT,
        subscription_days=settings.SUBSCRIPTION_DAYS
    )

    tier_name = billing_service.get_tier_name(tier)
    if success and sub_info:
        promo_text = " (по специальной цене Early-Bird 🔥)" if sub_info.get("is_early_bird") else ""
        await message.answer(
            f"🎉 <b>Поздравляем! Оплата успешно проведена!</b>\n\n"
            f"Вам подключен тариф <b>{tier_name}</b>{promo_text}.\n"
            f"• Срок действия: <b>30 дней</b>\n"
            f"• Действует до: <code>{sub_info['expires_at']}</code>\n\n"
            "Теперь вы можете загружать личные документы и задавать вопросы нейросети!"
        )
    else:
        await message.answer(f"⚠️ Платеж принят, статус обработки: {msg}")
