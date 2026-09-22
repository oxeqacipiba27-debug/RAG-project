from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Текстовые константы кнопок нижнего меню (Reply Keyboard)
BTN_TARIFFS = "💎 Тарифы и подписка"
BTN_MY_SUB = "📋 Моя подписка"
BTN_MY_FILES = "📁 Мои документы"
BTN_CLEAR_DB = "🗑 Очистить базу"
BTN_HELP = "📖 Справка"
BTN_RESET_SESSION = "🔄 Новая тема"

# Кнопки для администраторов
BTN_ADMIN_CONSOLE = "🛠 Админ-консоль"
BTN_ADMIN_BILLING = "💰 Биллинг и чеки"
BTN_ADMIN_STATS = "📊 Статистика"

# Кнопка принудительного вызова меню
BTN_RESTORE_MENU = "🔘 Главное меню"

# Множество всех служебных кнопок для фильтрации RAG (чтобы текст кнопок не отправлялся в LLM)
ALL_MENU_BUTTONS = {
    BTN_TARIFFS,
    BTN_MY_SUB,
    BTN_MY_FILES,
    BTN_CLEAR_DB,
    BTN_HELP,
    BTN_RESET_SESSION,
    "🔄 Новая тема",
    "Новая тема",
    "Сбросить контекст",
    BTN_ADMIN_CONSOLE,
    BTN_ADMIN_BILLING,
    BTN_ADMIN_STATS,
    BTN_RESTORE_MENU,
    "🔘 Меню",
    "Меню",
    "Главное меню",
    "Кнопки",
    "Вернуть кнопки",
    "Верните кнопки",
    "Где кнопки",
    "Показать кнопки",
    "Покажи кнопки",
    "Справка",
    "Помощь",
    "Мои файлы",
    "Мои документы",
    "Очистить базу",
    "Тарифы",
    "Подписка",
    "Админ",
    "Админ-консоль",
    "Статистика",
}


def get_main_reply_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Главная постоянная Reply-клавиатура внизу экрана."""
    if is_admin:
        keyboard = [
            [KeyboardButton(text=BTN_RESET_SESSION), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_TARIFFS), KeyboardButton(text=BTN_MY_SUB)],
            [KeyboardButton(text=BTN_MY_FILES), KeyboardButton(text=BTN_CLEAR_DB)],
            [KeyboardButton(text=BTN_ADMIN_CONSOLE), KeyboardButton(text=BTN_ADMIN_BILLING)],
            [KeyboardButton(text=BTN_ADMIN_STATS)],
        ]
    else:
        keyboard = [
            [KeyboardButton(text=BTN_RESET_SESSION), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_TARIFFS), KeyboardButton(text=BTN_MY_SUB)],
            [KeyboardButton(text=BTN_MY_FILES), KeyboardButton(text=BTN_CLEAR_DB)],
        ]

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder="Задайте вопрос или отправьте файл..."
    )


def get_start_inline_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Быстрые действия под стартовым приветствием."""
    inline_keyboard = [
        [
            InlineKeyboardButton(text="💎 Выбрать тариф (скидка 40%)", callback_data="sub:tariffs")
        ],
        [
            InlineKeyboardButton(text="📁 Мои документы", callback_data="user:docs:list"),
            InlineKeyboardButton(text="📋 Моя подписка", callback_data="sub:my_sub"),
        ]
    ]

    if is_admin:
        inline_keyboard.append([
            InlineKeyboardButton(text="🛠 Админ-консоль", callback_data="admin:menu"),
            InlineKeyboardButton(text="💰 Биллинг", callback_data="admin:billing:refresh"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
