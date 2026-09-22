from typing import Any, Dict, List, Set
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_main_kb() -> InlineKeyboardMarkup:
    """Главная клавиатура административной консоли."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📁 База знаний (файлы)", callback_data="admin:docs:list"),
        InlineKeyboardButton(text="👥 Управление доступом", callback_data="admin:users:list")
    )
    builder.row(
        InlineKeyboardButton(text="💰 Биллинг и подписки", callback_data="admin:billing:refresh"),
        InlineKeyboardButton(text="📊 Статистика и метрики", callback_data="admin:stats")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin:menu"),
        InlineKeyboardButton(text="❌ Закрыть консоль", callback_data="admin:close")
    )
    return builder.as_markup()


def get_docs_list_kb(docs_info: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Клавиатура со списком документов и кнопками их удаления."""
    builder = InlineKeyboardBuilder()

    if not docs_info:
        builder.row(
            InlineKeyboardButton(text="ℹ️ Документов пока нет", callback_data="admin:docs:noop")
        )
    else:
        for idx, doc in enumerate(docs_info):
            source = doc["source"]
            chunks = doc["chunk_count"]
            # Обрезаем имя файла для отображения, если оно слишком длинное
            display_name = (source[:24] + "…") if len(source) > 25 else source
            builder.row(
                InlineKeyboardButton(
                    text=f"📄 {display_name} ({chunks} ч.)",
                    callback_data=f"admin:doc_info:{idx}"
                ),
                InlineKeyboardButton(
                    text="🗑️ Удалить",
                    callback_data=f"admin:doc_del:{idx}"
                )
            )

        builder.row(
            InlineKeyboardButton(
                text="⚠️ Очистить всю базу",
                callback_data="admin:doc_clear_confirm"
            )
        )

    builder.row(
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="admin:menu")
    )
    return builder.as_markup()


def get_doc_confirm_delete_kb(doc_index: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления конкретного документа."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"admin:doc_do_del:{doc_index}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:docs:list")
    )
    return builder.as_markup()


def get_doc_clear_confirm_kb() -> InlineKeyboardMarkup:
    """Подтверждение полной очистки базы знаний."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💥 Да, очистить всё", callback_data="admin:doc_do_clear"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:docs:list")
    )
    return builder.as_markup()


def get_users_list_kb(users: List[int], admin_ids: Set[int]) -> InlineKeyboardMarkup:
    """Клавиатура со списком пользователей и кнопками отзыва доступа."""
    builder = InlineKeyboardBuilder()

    for uid in users:
        is_adm = uid in admin_ids
        role = " (Админ)" if is_adm else ""
        if is_adm:
            builder.row(
                InlineKeyboardButton(text=f"👑 {uid}{role}", callback_data="admin:noop")
            )
        else:
            builder.row(
                InlineKeyboardButton(text=f"👤 {uid}", callback_data="admin:noop"),
                InlineKeyboardButton(text="🚫 Забанить", callback_data=f"admin:user_ban:{uid}")
            )

    builder.row(
        InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin:user_add_prompt")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="admin:menu")
    )
    return builder.as_markup()


def get_cancel_admin_fsm_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены ввода в админке."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:cancel_fsm")
    )
    return builder.as_markup()


def get_back_to_menu_kb() -> InlineKeyboardMarkup:
    """Кнопка возврата в главное меню админ-панели."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ В главное меню", callback_data="admin:menu")
    )
    return builder.as_markup()
