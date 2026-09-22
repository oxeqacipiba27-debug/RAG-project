from typing import Any, Dict, List
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_user_docs_list_kb(docs_info: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Клавиатура со списком личных документов пользователя."""
    builder = InlineKeyboardBuilder()

    if not docs_info:
        builder.row(
            InlineKeyboardButton(text="ℹ️ У вас пока нет загруженных файлов", callback_data="user:noop")
        )
    else:
        for idx, doc in enumerate(docs_info):
            source = doc["source"]
            chunks = doc["chunk_count"]
            display_name = (source[:22] + "…") if len(source) > 23 else source
            builder.row(
                InlineKeyboardButton(
                    text=f"📄 {display_name} ({chunks} ч.)",
                    callback_data=f"user:doc_info:{idx}"
                ),
                InlineKeyboardButton(
                    text="🗑️ Удалить",
                    callback_data=f"user:doc_del:{idx}"
                )
            )

        builder.row(
            InlineKeyboardButton(
                text="⚠️ Очистить мою базу знаний",
                callback_data="user:doc_clear_confirm"
            )
        )

    builder.row(
        InlineKeyboardButton(text="🔘 Главное меню", callback_data="user:show_menu"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="user:close")
    )
    return builder.as_markup()


def get_user_doc_confirm_delete_kb(doc_index: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления документа пользователем."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"user:doc_do_del:{doc_index}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="user:docs:list")
    )
    return builder.as_markup()


def get_user_clear_confirm_kb() -> InlineKeyboardMarkup:
    """Подтверждение полной очистки персональной базы пользователя."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💥 Да, удалить всё", callback_data="user:doc_do_clear"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="user:docs:list")
    )
    return builder.as_markup()
