from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_tariffs_kb(
    basic_price: int,
    pro_price: int,
    is_promo: bool = True,
    remaining_slots: int = 10
) -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа с актуальными динамическими ценами."""
    promo_badge = "🔥 " if is_promo else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{promo_badge}Тариф «Базовый» — {basic_price:,} ₽/мес".replace(",", " "),
                    callback_data="sub:select:basic"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{promo_badge}Тариф «ПРО» — {pro_price:,} ₽/мес".replace(",", " "),
                    callback_data="sub:select:pro"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Моя подписка",
                    callback_data="sub:my_sub"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть",
                    callback_data="user:close"
                )
            ]
        ]
    )


def get_tier_checkout_kb(tier: str, price: int, payment_mode: str = "MANUAL") -> InlineKeyboardMarkup:
    """Клавиатура перехода к оплате выбранного тарифа."""
    rows = []
    price_str = f"{price:,} ₽".replace(",", " ")

    if payment_mode.upper() == "TELEGRAM_PAYMENTS":
        rows.append([
            InlineKeyboardButton(
                text=f"💳 Оплатить картой онлайн ({price_str})",
                callback_data=f"sub:pay_online:{tier}:{price}"
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="📱 Оплатить по СБП (перевод по реквизитам)",
                callback_data=f"sub:pay_manual:{tier}:{price}"
            )
        ])
    else:
        # По умолчанию ручной режим P2P/СБП
        rows.append([
            InlineKeyboardButton(
                text=f"💳 Оплатить по СБП / Карте ({price_str})",
                callback_data=f"sub:pay_manual:{tier}:{price}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад к тарифам",
            callback_data="sub:tariffs"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_receipt_upload_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены отправки чека."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить отправку",
                    callback_data="sub:cancel_upload"
                )
            ]
        ]
    )


def get_admin_payment_approval_kb(payment_id: str) -> InlineKeyboardMarkup:
    """Кнопки решения администратора по чеку оплаты."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить оплату",
                    callback_data=f"admin:pay_confirm:{payment_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"admin:pay_reject:{payment_id}"
                )
            ]
        ]
    )


def get_my_sub_kb(has_sub: bool = False) -> InlineKeyboardMarkup:
    """Кнопки в меню статуса подписки."""
    action_text = "🔄 Продлить / Сменить тариф" if has_sub else "💳 Выбрать тариф"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=action_text,
                    callback_data="sub:tariffs"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть",
                    callback_data="user:close"
                )
            ]
        ]
    )


def get_admin_billing_kb() -> InlineKeyboardMarkup:
    """Клавиатура админ-панели биллинга."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить сводку",
                    callback_data="admin:billing:refresh"
                ),
                InlineKeyboardButton(
                    text="📋 Ожидающие чеки",
                    callback_data="admin:billing:pending"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Выдать подписку",
                    callback_data="admin:billing:grant_prompt"
                ),
                InlineKeyboardButton(
                    text="🛠 Консоль админа",
                    callback_data="admin:menu"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Закрыть",
                    callback_data="admin:close"
                )
            ]
        ]
    )


def get_cancel_billing_fsm_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены ввода в биллинге."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="admin:billing:cancel_fsm"
                )
            ]
        ]
    )
