from pathlib import Path
from typing import Any, Dict, List
from aiogram import Bot, F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.config import Settings
from bot.keyboards.admin_kb import (
    get_admin_main_kb,
    get_back_to_menu_kb,
    get_cancel_admin_fsm_kb,
    get_doc_clear_confirm_kb,
    get_doc_confirm_delete_kb,
    get_docs_list_kb,
    get_users_list_kb,
)
from bot.keyboards.menu_kb import BTN_ADMIN_CONSOLE, BTN_ADMIN_STATS
from bot.services.db_service import DatabaseService
from bot.services.ingest import DocumentIngestionService
from bot.services.queue_manager import QueueManager
from bot.services.vector_db import VectorDBService

router = Router(name="admin_router")

SUPPORTED_EXTENSIONS = {
    # PDF & Plain Text
    ".pdf", ".txt", ".md",
    # Microsoft Word & OpenDocument Text
    ".docx", ".doc", ".docm", ".dotx", ".dotm", ".rtf", ".odt", ".ott",
    # Microsoft Excel & OpenDocument Spreadsheet
    ".xlsx", ".xls", ".xlsm", ".xltx", ".xltm", ".csv", ".ods", ".ots",
    # Microsoft PowerPoint & OpenDocument Presentation
    ".pptx", ".ppt", ".pptm", ".ppsx", ".pps", ".potx", ".potm", ".odp", ".otp",
}


class AdminUserStates(StatesGroup):
    waiting_for_user_id = State()



def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.ADMIN_IDS


def format_admin_dashboard(
    db_stats: dict,
    my_chunks: int,
    my_sources_count: int,
    sys_overview: dict,
    q_status: dict,
    settings: Settings
) -> str:
    """Форматирование главного экрана админ-панели."""
    inf_status = "🔴 Занят" if q_status["is_busy"] else "🟢 Свободен"
    return (
        "🛠 <b>Консоль администратора SchoolX RAG</b>\n\n"
        "🔒 <b>Архитектура:</b> Multi-Tenant (отдельная БД для каждого ID)\n\n"
        "👤 <b>Ваша личная база знаний:</b>\n"
        f"• Документов: <b>{my_sources_count}</b>\n"
        f"• Векторизованных чанков: <b>{my_chunks}</b>\n\n"
        "🌐 <b>Всего в системе:</b>\n"
        f"• Пользовательских баз: <b>{sys_overview['total_collections']}</b>\n"
        f"• Суммарно чанков: <b>{sys_overview['total_chunks']}</b>\n"
        f"• Пользователей в белом списке: <b>{db_stats['total_allowed_users']}</b>\n\n"
        "⚡ <b>Статус инференса (LM Studio):</b>\n"
        f"• Состояние: <b>{inf_status}</b> (очередь: <b>{q_status['queue_length']}</b>)\n"
        f"• Модель: <code>{settings.LM_STUDIO_MODEL}</code>\n\n"
        "Выберите раздел для управления:"
    )


# ==============================================================================
# Команды консоли
# ==============================================================================

@router.message(or_f(Command("admin"), F.text.in_([BTN_ADMIN_CONSOLE, "Админ", "Админ-консоль", "Панель"])))
async def cmd_admin_console(
    message: Message,
    settings: Settings,
    db_service: DatabaseService,
    vector_db: VectorDBService,
    queue_manager: QueueManager
) -> None:
    """Открытие интерактивной консоли администратора: /admin."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    admin_id = message.from_user.id
    db_stats = await db_service.get_stats()
    my_chunks = await vector_db.count(admin_id)
    my_sources = await vector_db.get_unique_sources(admin_id)
    sys_overview = await vector_db.get_system_overview()
    q_status = queue_manager.get_status()

    text = format_admin_dashboard(db_stats, my_chunks, len(my_sources), sys_overview, q_status, settings)
    await message.answer(text, reply_markup=get_admin_main_kb())


@router.message(Command("add_user"))
async def cmd_add_user(
    message: Message,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Добавление пользователя в белый список: /add_user <tg_id>."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    args = (message.text or "").strip().split()
    if len(args) < 2:
        await message.answer(
            "ℹ️ <b>Формат команды:</b> <code>/add_user &lt;telegram_id&gt;</code>\n"
            "Пример: <code>/add_user 123456789</code>"
        )
        return

    target_str = args[1]
    if not (target_str.isdigit() or (target_str.startswith("-") and target_str[1:].isdigit())):
        await message.answer("❌ <b>Ошибка:</b> Telegram ID должен быть числом.")
        return

    target_id = int(target_str)
    await db_service.add_user(target_id, added_by=message.from_user.id)
    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> успешно добавлен в белый список.\n"
        f"Для него создана персональная изолированная база знаний."
    )
    logger.info(f"Admin {message.from_user.id} whitelisted user {target_id}.")


@router.message(Command("ban_user"))
async def cmd_ban_user(
    message: Message,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Удаление пользователя из белого списка: /ban_user <tg_id>."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    args = (message.text or "").strip().split()
    if len(args) < 2:
        await message.answer(
            "ℹ️ <b>Формат команды:</b> <code>/ban_user &lt;telegram_id&gt;</code>\n"
            "Пример: <code>/ban_user 123456789</code>"
        )
        return

    target_str = args[1]
    if not (target_str.isdigit() or (target_str.startswith("-") and target_str[1:].isdigit())):
        await message.answer("❌ <b>Ошибка:</b> Telegram ID должен быть числом.")
        return

    target_id = int(target_str)
    if target_id in settings.ADMIN_IDS:
        await message.answer("⚠️ <b>Нельзя удалить системного администратора.</b>")
        return

    deleted = await db_service.ban_user(target_id)
    if deleted:
        await message.answer(f"🚫 Пользователь <code>{target_id}</code> удален из белого списка.")
    else:
        await message.answer(f"ℹ️ Пользователь <code>{target_id}</code> не найден в базе разрешенных.")


@router.message(Command("del_file"))
async def cmd_del_file(
    message: Message,
    settings: Settings,
    vector_db: VectorDBService,
    db_service: DatabaseService,
) -> None:
    """Текстовая команда удаления файла из личной базы: /del_file <filename>."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    admin_id = message.from_user.id
    args = (message.text or "").strip().split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "ℹ️ <b>Формат команды:</b> <code>/del_file &lt;имя_файла&gt;</code>\n"
            "Пример: <code>/del_file rules.txt</code>\n"
            "<i>(Или воспользуйтесь интерактивным списком в /admin)</i>"
        )
        return

    target_file = args[1].strip()
    deleted_chunks = await vector_db.delete_source(admin_id, target_file)
    await db_service.delete_document(admin_id, target_file)

    local_file = settings.DOCS_STORAGE_DIR / str(admin_id) / target_file
    if local_file.exists():
        try:
            local_file.unlink()
        except Exception as e:
            logger.warning(f"Could not delete physical file '{local_file}': {e}")

    if deleted_chunks > 0:
        await message.answer(
            f"✅ Документ <b>«{target_file}»</b> успешно удален из вашей базы знаний.\n"
            f"Удалено чанков: <b>{deleted_chunks}</b>."
        )
    else:
        await message.answer(f"⚠️ Документ <b>«{target_file}»</b> не найден в вашей базе знаний.")


@router.message(or_f(Command("stats"), F.text.in_([BTN_ADMIN_STATS, "Статистика", "Метрики"])))
async def cmd_stats(
    message: Message,
    settings: Settings,
    db_service: DatabaseService,
    vector_db: VectorDBService,
    queue_manager: QueueManager
) -> None:
    """Вывод подробной статистики системы: /stats."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    admin_id = message.from_user.id
    db_stats = await db_service.get_stats()
    my_chunks = await vector_db.count(admin_id)
    my_sources = await vector_db.get_unique_sources(admin_id)
    sys_overview = await vector_db.get_system_overview()
    q_status = queue_manager.get_status()

    sources_preview = ", ".join(my_sources[:5]) if my_sources else "нет документов"
    if len(my_sources) > 5:
        sources_preview += f" (+ еще {len(my_sources) - 5})"

    inference_state = "🔴 Занят генерацией" if q_status["is_busy"] else "🟢 Свободен (ожидает)"

    stats_text = (
        "📊 <b>Статистика системы SchoolX RAG:</b>\n\n"
        "👥 <b>Пользователи:</b>\n"
        f"• Авторизовано в БД: <b>{db_stats['total_allowed_users']}</b>\n"
        f"• Системных администраторов: <b>{len(settings.ADMIN_IDS)}</b>\n\n"
        "📚 <b>Векторные хранилища (ChromaDB + FastEmbed):</b>\n"
        f"• Всего изолированных баз (пользователей): <b>{sys_overview['total_collections']}</b>\n"
        f"• Суммарно чанков по всем базам: <b>{sys_overview['total_chunks']}</b>\n"
        f"• В вашей личной базе: <b>{my_chunks}</b> чанков (файлы: <i>{sources_preview}</i>)\n"
        f"• Модель эмбеддингов: <code>{settings.EMBEDDING_MODEL_NAME}</code> (CPU)\n\n"
        "⚡ <b>Очередь инференса (RTX 4070 12GB - 1 поток):</b>\n"
        f"• Задач в очереди: <b>{q_status['queue_length']}</b>\n"
        f"• Статус инференса: <b>{inference_state}</b>\n\n"
        "💬 <b>Активность и качество:</b>\n"
        f"• Всего обработано запросов: <b>{db_stats['total_queries']}</b>\n"
        f"• Положительных оценок (👍): <b>{db_stats['positive_feedback']}</b>\n"
        f"• Отрицательных оценок (👎): <b>{db_stats['negative_feedback']}</b>"
    )
    await message.answer(stats_text)


# ==============================================================================
# Интерактивные Callback-обработчики админ-панели
# ==============================================================================

@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService,
    vector_db: VectorDBService,
    queue_manager: QueueManager
) -> None:
    """Возврат на главный экран админ-консоли."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    admin_id = callback.from_user.id
    db_stats = await db_service.get_stats()
    my_chunks = await vector_db.count(admin_id)
    my_sources = await vector_db.get_unique_sources(admin_id)
    sys_overview = await vector_db.get_system_overview()
    q_status = queue_manager.get_status()

    text = format_admin_dashboard(db_stats, my_chunks, len(my_sources), sys_overview, q_status, settings)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_admin_main_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:docs:list")
async def cb_admin_docs_list(
    callback: CallbackQuery,
    settings: Settings,
    vector_db: VectorDBService
) -> None:
    """Отображение списка документов личной базы администратора с возможностью удаления."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    admin_id = callback.from_user.id
    docs_info = await vector_db.get_sources_stats(admin_id)
    total_chunks = sum(d["chunk_count"] for d in docs_info)

    text = (
        "📁 <b>Ваша личная база знаний (Администратор)</b>\n\n"
        f"Всего загружено документов: <b>{len(docs_info)}</b>\n"
        f"Всего фрагментов (чанков): <b>{total_chunks}</b>\n\n"
        "<i>Нажмите «🗑️ Удалить» рядом с файлом, чтобы извлечь его из вашей базы:</i>"
    )

    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_docs_list_kb(docs_info))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:doc_del:"))
async def cb_admin_doc_del_confirm(
    callback: CallbackQuery,
    settings: Settings,
    vector_db: VectorDBService
) -> None:
    """Запрос подтверждения удаления файла."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    admin_id = callback.from_user.id
    idx_str = callback.data.split(":")[2]
    if not idx_str.isdigit():
        await callback.answer()
        return

    idx = int(idx_str)
    docs_info = await vector_db.get_sources_stats(admin_id)
    if idx >= len(docs_info):
        await callback.answer("Файл уже удален или не найден.", show_alert=True)
        return

    target_doc = docs_info[idx]
    filename = target_doc["source"]
    chunks = target_doc["chunk_count"]

    text = (
        f"⚠️ <b>Подтверждение удаления документа</b>\n\n"
        f"Файл: <b>«{filename}»</b>\n"
        f"Количество чанков в вашей базе: <b>{chunks}</b>\n\n"
        "Вы действительно хотите удалить этот документ?"
    )

    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_doc_confirm_delete_kb(idx))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:doc_do_del:"))
async def cb_admin_doc_do_delete(
    callback: CallbackQuery,
    settings: Settings,
    vector_db: VectorDBService,
    db_service: DatabaseService,
) -> None:
    """Фактическое удаление документа из персональной базы админа."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    admin_id = callback.from_user.id
    idx_str = callback.data.split(":")[2]
    if not idx_str.isdigit():
        await callback.answer()
        return

    idx = int(idx_str)
    docs_info = await vector_db.get_sources_stats(admin_id)
    if idx >= len(docs_info):
        await callback.answer("Файл уже удален.", show_alert=True)
        return

    filename = docs_info[idx]["source"]
    deleted_chunks = await vector_db.delete_source(admin_id, filename)
    await db_service.delete_document(admin_id, filename)

    local_file = settings.DOCS_STORAGE_DIR / str(admin_id) / filename
    if local_file.exists():
        try:
            local_file.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete disk file '{local_file}': {e}")

    await callback.answer(f"Файл «{filename}» удален ({deleted_chunks} чанков)!", show_alert=True)

    updated_docs = await vector_db.get_sources_stats(admin_id)
    total_chunks = sum(d["chunk_count"] for d in updated_docs)
    text = (
        "📁 <b>Ваша личная база знаний (Администратор)</b>\n\n"
        f"Всего загружено документов: <b>{len(updated_docs)}</b>\n"
        f"Всего фрагментов (чанков): <b>{total_chunks}</b>\n\n"
        "<i>Нажмите «🗑️ Удалить» рядом с файлом, чтобы извлечь его из базы:</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_docs_list_kb(updated_docs))


@router.callback_query(F.data == "admin:doc_clear_confirm")
async def cb_admin_doc_clear_confirm(callback: CallbackQuery, settings: Settings) -> None:
    """Подтверждение полной очистки базы знаний."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    text = (
        "💥 <b>Очистка вашей базы знаний</b>\n\n"
        "Вы собираетесь удалить ВСЕ проиндексированные документы из вашей личной базы.\n"
        "Это действие необратимо!\n\n"
        "Вы уверены?"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_doc_clear_confirm_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:doc_do_clear")
async def cb_admin_doc_do_clear(
    callback: CallbackQuery,
    settings: Settings,
    vector_db: VectorDBService,
    db_service: DatabaseService,
) -> None:
    """Полная очистка личной коллекции админа."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    admin_id = callback.from_user.id
    cleared_count = await vector_db.clear_all(admin_id)
    await db_service.clear_user_documents(admin_id)
    await callback.answer(f"База очищена! Удалено {cleared_count} чанков.", show_alert=True)

    updated_docs = await vector_db.get_sources_stats(admin_id)
    text = (
        "📁 <b>Ваша личная база знаний</b>\n\n"
        "База знаний пуста. Загрузите новые документы, отправив их файлом в чат."
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_docs_list_kb(updated_docs))


@router.callback_query(F.data == "admin:users:list")
async def cb_admin_users_list(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Отображение списка пользователей."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    allowed_users = await db_service.get_all_allowed_users()
    text = (
        "👥 <b>Управление доступом пользователей</b>\n\n"
        f"Всего авторизовано: <b>{len(allowed_users)}</b>\n\n"
        "<i>У каждого пользователя изолированная персональная база данных.</i>\n"
        "Нажмите «🚫 Забанить», чтобы закрыть доступ:"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=get_users_list_kb(allowed_users, settings.ADMIN_IDS)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user_ban:"))
async def cb_admin_user_ban(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Бан пользователя через кнопку в списке."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    uid_str = callback.data.split(":")[2]
    if not uid_str.isdigit():
        await callback.answer()
        return

    target_id = int(uid_str)
    if target_id in settings.ADMIN_IDS:
        await callback.answer("Нельзя забанить администратора.", show_alert=True)
        return

    await db_service.ban_user(target_id)
    await callback.answer(f"Пользователь {target_id} забанен.", show_alert=False)

    allowed_users = await db_service.get_all_allowed_users()
    text = (
        "👥 <b>Управление доступом пользователей</b>\n\n"
        f"Всего авторизовано: <b>{len(allowed_users)}</b>\n\n"
        "Нажмите «🚫 Забанить», чтобы закрыть доступ:"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=get_users_list_kb(allowed_users, settings.ADMIN_IDS)
        )


@router.callback_query(F.data == "admin:user_add_prompt")
async def cb_admin_user_add_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings
) -> None:
    """Запуск интерактивного ввода Telegram ID пользователя."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    await state.set_state(AdminUserStates.waiting_for_user_id)
    text = (
        "➕ <b>Добавление пользователя в систему</b>\n\n"
        "Отправьте <b>Telegram ID</b> пользователя (число), которому необходимо предоставить доступ.\n\n"
        "<i>Для отмены нажмите кнопку ниже:</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_cancel_admin_fsm_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:cancel_fsm")
async def cb_admin_cancel_fsm(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Отмена FSM-ввода в админке."""
    await state.clear()
    allowed_users = await db_service.get_all_allowed_users()
    text = (
        "👥 <b>Управление доступом пользователей</b>\n\n"
        f"Всего авторизовано: <b>{len(allowed_users)}</b>\n\n"
        "Нажмите «🚫 Забанить», чтобы закрыть доступ:"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=get_users_list_kb(allowed_users, settings.ADMIN_IDS)
        )
    await callback.answer("Ввод отменен.")


@router.message(AdminUserStates.waiting_for_user_id)
async def handle_admin_user_id_input(
    message: Message,
    state: FSMContext,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Обработка введенного администратором ID пользователя."""
    if not message.from_user or not is_admin(message.from_user.id, settings):
        return

    raw = (message.text or "").strip()
    if not (raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit())):
        await message.answer(
            "❌ <b>Ошибка:</b> Telegram ID должен быть числом.\n"
            "Пожалуйста, введите корректный ID или нажмите «Отмена»:",
            reply_markup=get_cancel_admin_fsm_kb()
        )
        return

    target_id = int(raw)
    await db_service.add_user(target_id, added_by=message.from_user.id)
    await state.clear()

    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> успешно добавлен в систему!\n"
        "Для него создана персональная изолированная база знаний."
    )
    logger.info(f"Admin {message.from_user.id} added user {target_id} via button FSM.")


@router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService,
    vector_db: VectorDBService,
    queue_manager: QueueManager
) -> None:
    """Отображение подробной статистики с кнопкой Назад."""
    if not callback.from_user or not is_admin(callback.from_user.id, settings):
        await callback.answer("Доступ запрещен.", show_alert=True)
        return

    admin_id = callback.from_user.id
    db_stats = await db_service.get_stats()
    my_chunks = await vector_db.count(admin_id)
    my_sources = await vector_db.get_unique_sources(admin_id)
    sys_overview = await vector_db.get_system_overview()
    q_status = queue_manager.get_status()

    sources_preview = ", ".join(my_sources[:5]) if my_sources else "нет документов"
    if len(my_sources) > 5:
        sources_preview += f" (+ еще {len(my_sources) - 5})"

    inference_state = "🔴 Занят генерацией" if q_status["is_busy"] else "🟢 Свободен (ожидает)"

    stats_text = (
        "📊 <b>Детальная статистика SchoolX RAG:</b>\n\n"
        "👥 <b>Пользователи:</b>\n"
        f"• Авторизовано в БД: <b>{db_stats['total_allowed_users']}</b>\n"
        f"• Системных администраторов: <b>{len(settings.ADMIN_IDS)}</b>\n\n"
        "📚 <b>Векторные хранилища (ChromaDB + FastEmbed):</b>\n"
        f"• Всего изолированных баз (пользователей): <b>{sys_overview['total_collections']}</b>\n"
        f"• Суммарно чанков по всем базам: <b>{sys_overview['total_chunks']}</b>\n"
        f"• В вашей личной базе: <b>{my_chunks}</b> чанков (файлы: <i>{sources_preview}</i>)\n"
        f"• Модель эмбеддингов: <code>{settings.EMBEDDING_MODEL_NAME}</code> (CPU)\n\n"
        "⚡ <b>Очередь инференса (RTX 4070 12GB - 1 поток):</b>\n"
        f"• Задач в очереди: <b>{q_status['queue_length']}</b>\n"
        f"• Статус инференса: <b>{inference_state}</b>\n\n"
        "💬 <b>Активность и качество:</b>\n"
        f"• Всего обработано запросов: <b>{db_stats['total_queries']}</b>\n"
        f"• Положительных оценок (👍): <b>{db_stats['positive_feedback']}</b>\n"
        f"• Отрицательных оценок (👎): <b>{db_stats['negative_feedback']}</b>"
    )

    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(stats_text, reply_markup=get_back_to_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:close")
async def cb_admin_close(callback: CallbackQuery) -> None:
    """Закрытие сообщения админ-консоли."""
    if callback.message and isinstance(callback.message, Message):
        try:
            await callback.message.delete()
        except Exception:
            pass
    await callback.answer("Консоль закрыта.")


@router.callback_query(F.data == "admin:noop")
async def cb_admin_noop(callback: CallbackQuery) -> None:
    await callback.answer()
