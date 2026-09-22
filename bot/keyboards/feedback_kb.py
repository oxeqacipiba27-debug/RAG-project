from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_feedback_keyboard(log_id: int) -> InlineKeyboardMarkup:
    """Создание клавиатуры с кнопками оценки ответа (👍 / 👎) и кнопкой Меню."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👍 Полезно", callback_data=f"fb:like:{log_id}"),
        InlineKeyboardButton(text="👎 Не помогло", callback_data=f"fb:dislike:{log_id}")
    )
    builder.row(
        InlineKeyboardButton(text="🔘 Главное меню", callback_data="user:show_menu")
    )
    return builder.as_markup()


def get_feedback_voted_keyboard(rating: int) -> InlineKeyboardMarkup:
    """Отображение состояния после отправки оценки пользователем с сохранением кнопки Меню."""
    label = "✅ Спасибо за оценку! 👍" if rating == 1 else "✅ Спасибо за оценку! 👎"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=label, callback_data="fb:already_voted")
    )
    builder.row(
        InlineKeyboardButton(text="🔘 Главное меню", callback_data="user:show_menu")
    )
    return builder.as_markup()
