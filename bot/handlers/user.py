from pathlib import Path
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, or_f
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.config import Settings
from bot.keyboards.feedback_kb import (
    get_feedback_keyboard,
    get_feedback_voted_keyboard,
)
from bot.keyboards.menu_kb import (
    ALL_MENU_BUTTONS,
    BTN_CLEAR_DB,
    BTN_HELP,
    BTN_MY_FILES,
    get_main_reply_kb,
    get_start_inline_kb,
)
from bot.keyboards.user_kb import (
    get_user_clear_confirm_kb,
    get_user_doc_confirm_delete_kb,
    get_user_docs_list_kb,
)
from bot.services.db_service import DatabaseService
from bot.services.ingest import DocumentIngestionService
from bot.services.queue_manager import QueueManager
from bot.services.vector_db import VectorDBService
from bot.utils.formatter import split_message_text, strip_html_tags

router = Router(name="user_router")

SUPPORTED_USER_EXTENSIONS = {
    # PDF & Plain Text
    ".pdf", ".txt", ".md",
    # Microsoft Word & OpenDocument Text
    ".docx", ".doc", ".docm", ".dotx", ".dotm", ".rtf", ".odt", ".ott",
    # Microsoft Excel & OpenDocument Spreadsheet
    ".xlsx", ".xls", ".xlsm", ".xltx", ".xltm", ".csv", ".ods", ".ots",
    # Microsoft PowerPoint & OpenDocument Presentation
    ".pptx", ".ppt", ".pptm", ".ppsx", ".pps", ".potx", ".potm", ".odp", ".otp",
}


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    settings: Settings,
    vector_db: VectorDBService
) -> None:
    """Приветственное сообщение, установка нижнего меню и быстрые кнопки."""
    user_id = message.from_user.id if message.from_user else 0
    is_admin = user_id in settings.ADMIN_IDS
    my_chunks = await vector_db.count(user_id)

    welcome_text = (
        "👋 <b>Добро пожаловать в персональную RAG-систему SchoolX!</b>\n\n"
        "🔒 <b>Полная изоляция данных:</b>\n"
        "К вашему Telegram ID привязана <b>отдельная персональная база знаний</b>. "
        "Файлы других пользователей изолированы и вам недоступны.\n\n"
        f"📊 Сейчас в вашей базе: <b>{my_chunks}</b> фрагментов (чанков).\n\n"
        "💡 <b>Возможности:</b>\n"
        "• <b>Загрузка файлов:</b> Отправьте файл ("
        "<b>Документы:</b> <code>.docx</code>, <code>.odt</code>, <code>.doc</code>, <code>.rtf</code>; "
        "<b>Таблицы:</b> <code>.xlsx</code>, <code>.xls</code>, <code>.ods</code>, <code>.csv</code>; "
        "<b>Презентации:</b> <code>.pptx</code>, <code>.ppt</code>, <code>.odp</code>; "
        "<b>PDF/Текст:</b> <code>.pdf</code>, <code>.txt</code>, <code>.md</code>) в этот чат, и он сразу добавится в вашу личную базу и векторизуется.\n"
        "• <b>Вопросы к базе:</b> Напишите любой вопрос обычным текстом, и бот ответит строго по вашим документам.\n"
        "• <b>Управление:</b> Используйте удобные кнопки внизу экрана — вводить команды вручную больше не требуется!\n\n"
        "<i>На GPU действует очередь: строго 1 запрос единовременно для стабильности.</i>"
    )
    # Отправляем постоянное нижнее меню (Reply Keyboard)
    await message.answer(
        welcome_text,
        reply_markup=get_main_reply_kb(is_admin=is_admin)
    )
    # Выводим инлайн-кнопки быстрого старта
    await message.answer(
        "⚡ <b>Быстрый доступ:</b> Выберите нужное действие кнопкой:",
        reply_markup=get_start_inline_kb(is_admin=is_admin)
    )


@router.message(or_f(Command("help"), F.text.in_([BTN_HELP, "Справка", "Помощь"])))
async def cmd_help(message: Message) -> None:
    """Справочная информация."""
    help_text = (
        "📖 <b>Справка по персональной базе знаний:</b>\n\n"
        "1. <b>Как наполнить базу:</b>\n"
        "Отправьте боту документ (Word/ODT: .docx/.odt/.doc/.rtf, Excel: .xlsx/.xls/.ods/.csv, PowerPoint: .pptx/.ppt/.odp, PDF, TXT, MD). "
        "Бот автоматически извлечет текст и таблицы, разобьет их на фрагменты (чанки) и векторизует в вашей персональной базе.\n\n"
        "2. <b>Как задать вопрос:</b>\n"
        "Отправьте вопрос текстом. Бот найдет нужные места именно в ваших документах и сформирует точный ответ со ссылкой на источник и страницу.\n\n"
        "3. <b>Управление файлами:</b>\n"
        "• Кнопка «📁 Мои документы» — посмотреть загруженные документы и удалить ненужные;\n"
        "• Кнопка «🗑 Очистить базу» — полностью очистить вашу персональную базу.\n\n"
        "4. <b>Подписка и тарифы:</b>\n"
        "• Кнопка «💎 Тарифы и подписка» — витрина тарифов и скидок (Early-Bird -40%);\n"
        "• Кнопка «📋 Моя подписка» — срок действия текущей подписки.\n\n"
        "5. <b>Очередь:</b>\n"
        "Для защиты VRAM инференс выполняется последовательно. При высокой нагрузке бот сообщит вашу позицию в очереди."
    )
    await message.answer(help_text)


@router.message(or_f(Command("my_files"), F.text.in_([BTN_MY_FILES, "Мои файлы", "Мои документы"])))
async def cmd_my_files(message: Message, vector_db: VectorDBService) -> None:
    """Просмотр и управление личными документами пользователя."""
    user_id = message.from_user.id if message.from_user else 0
    docs = await vector_db.get_sources_stats(user_id)
    total_chunks = sum(d["chunk_count"] for d in docs)

    text = (
        "📂 <b>Ваша персональная база знаний</b>\n\n"
        f"• Загружено документов: <b>{len(docs)}</b>\n"
        f"• Всего векторизованных чанков: <b>{total_chunks}</b>\n\n"
        "<i>Вы можете удалить любой документ кнопкой ниже:</i>"
    )
    await message.answer(text, reply_markup=get_user_docs_list_kb(docs))


@router.message(or_f(Command("clear_my_db"), F.text.in_([BTN_CLEAR_DB, "Очистить базу"])))
async def cmd_clear_my_db(message: Message) -> None:
    """Запрос на полную очистку персональной базы."""
    text = (
        "⚠️ <b>Очистка персональной базы знаний</b>\n\n"
        "Вы уверены, что хотите удалить ВСЕ свои проиндексированные документы? "
        "Это действие необратимо."
    )
    await message.answer(text, reply_markup=get_user_clear_confirm_kb())


# ==============================================================================
# Загрузка личных документов пользователя
# ==============================================================================

@router.message(F.document)
async def handle_user_document_upload(
    message: Message,
    bot: Bot,
    settings: Settings,
    vector_db: VectorDBService,
    db_service: DatabaseService,
    ingest_service: DocumentIngestionService
) -> None:
    """Прием и индексация документов в персональную базу пользователя."""
    user_id = message.from_user.id if message.from_user else 0
    doc = message.document
    if not doc or not doc.file_name:
        await message.answer("❌ Не удалось получить файл.")
        return

    filename = doc.file_name
    file_ext = Path(filename).suffix.lower()

    if file_ext not in SUPPORTED_USER_EXTENSIONS:
        await message.answer(
            f"⚠️ <b>Формат «{file_ext}» не поддерживается.</b>\n\n"
            f"<b>Поддерживаемые форматы:</b>\n"
            f"• <b>Документы и текст:</b> <code>.docx</code>, <code>.odt</code>, <code>.doc</code>, <code>.rtf</code>, <code>.pdf</code>, <code>.txt</code>, <code>.md</code>\n"
            f"• <b>Таблицы:</b> <code>.xlsx</code>, <code>.xls</code>, <code>.ods</code>, <code>.csv</code>\n"
            f"• <b>Презентации:</b> <code>.pptx</code>, <code>.ppt</code>, <code>.odp</code>"
        )
        return

    status_msg = await message.answer(
        f"⏳ <b>Документ «{filename}» получен.</b>\n"
        f"Выполняется извлечение текста, таблиц и векторизация на CPU..."
    )

    # Изолированная директория для пользователя
    user_docs_dir = settings.DOCS_STORAGE_DIR / str(user_id)
    user_docs_dir.mkdir(parents=True, exist_ok=True)
    local_path = user_docs_dir / filename

    try:
        await bot.download(doc, destination=local_path)
        logger.info(f"User {user_id}: Downloaded document '{filename}' to '{local_path}'.")

        # Индексация в персональную коллекцию с учетом настроек пользователя
        user_rag_cfg = settings.get_user_rag_config(user_id)
        chunks_added = await ingest_service.ingest_file(
            user_id=user_id,
            file_path=local_path,
            original_filename=filename,
            vector_db=vector_db,
            chunk_size=user_rag_cfg.chunk_size,
            chunk_overlap=user_rag_cfg.chunk_overlap,
            db_service=db_service,
        )

        total_chunks = await vector_db.count(user_id)
        await status_msg.edit_text(
            f"✅ <b>Документ «{filename}» успешно добавлен и векторизован в базе знаний!</b>\n\n"
            f"• Добавлено новых фрагментов (чанков): <b>{chunks_added}</b>\n"
            f"• Всего фрагментов в вашей базе: <b>{total_chunks}</b>\n"
            f"• Запись о файле сохранена в БД.\n\n"
            f"Теперь вы можете задавать любые вопросы по содержанию этого документа."
        )
        logger.info(f"User {user_id}: Ingestion success for '{filename}'.")

    except Exception as e:
        logger.exception(f"Error during user ingestion for '{filename}': {e}")
        err_msg = str(e)
        if "Bad offset for central directory" in err_msg or "BadZipFile" in err_msg:
            user_facing_err = (
                "Файл поврежден или имеет неверный формат архива (BadZipFile). "
                "Пожалуйста, проверьте целостность файла, сохраните его заново и повторите отправку."
            )
        elif "TimeoutError" in type(e).__name__ or "ClientConnectionError" in err_msg:
            user_facing_err = "Таймаут скачивания файла из Telegram. Пожалуйста, попробуйте отправить файл еще раз."
        else:
            user_facing_err = err_msg

        await status_msg.edit_text(
            f"❌ <b>Ошибка при обработке документа «{filename}»:</b>\n<code>{user_facing_err}</code>"
        )


# ==============================================================================
# Вопросы к RAG
# ==============================================================================

@router.message(F.text & ~F.text.startswith("/") & ~F.text.in_(ALL_MENU_BUTTONS))
async def handle_user_query(
    message: Message,
    bot: Bot,
    queue_manager: QueueManager
) -> None:
    """Обработка текстового вопроса пользователя через изолированную очередь RAG."""
    query = (message.text or "").strip()
    if not query:
        return

    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id

    try:
        answer, log_id, queue_msg_id = await queue_manager.submit_query(
            user_id=user_id,
            chat_id=chat_id,
            query=query,
            bot=bot
        )

        if queue_msg_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=queue_msg_id)
            except Exception:
                pass

        # Отправка ответа: разбиение на части при превышении лимита Telegram и fallback при ошибках HTML
        chunks = split_message_text(answer, max_length=4000)
        for i, chunk in enumerate(chunks):
            is_last = (i == len(chunks) - 1)
            kb = get_feedback_keyboard(log_id) if is_last else None
            try:
                await message.answer(text=chunk, reply_markup=kb)
            except TelegramBadRequest as tb_err:
                logger.warning(
                    f"Telegram HTML parsing error for user {user_id}: {tb_err}. "
                    "Falling back to stripped plain text."
                )
                await message.answer(text=strip_html_tags(chunk), reply_markup=kb)

    except Exception as exc:
        logger.error(f"Error handling query from user {user_id}: {exc}")
        await message.answer(
            "❌ <b>Произошла ошибка при обработке запроса.</b>\n"
            "Возможно, локальный сервер LM Studio временно недоступен. "
            "Пожалуйста, попробуйте снова через минуту или обратитесь к администратору."
        )


# ==============================================================================
# Callback-обработчики пользователя
# ==============================================================================

@router.callback_query(F.data == "user:docs:list")
async def cb_user_docs_list(callback: CallbackQuery, vector_db: VectorDBService) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    docs = await vector_db.get_sources_stats(user_id)
    total_chunks = sum(d["chunk_count"] for d in docs)

    text = (
        "📂 <b>Ваша персональная база знаний</b>\n\n"
        f"• Загружено документов: <b>{len(docs)}</b>\n"
        f"• Всего векторизованных чанков: <b>{total_chunks}</b>\n\n"
        "<i>Вы можете удалить любой документ кнопкой ниже:</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_docs_list_kb(docs))
    await callback.answer()


@router.callback_query(F.data.startswith("user:doc_del:"))
async def cb_user_doc_del(callback: CallbackQuery, vector_db: VectorDBService) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    idx_str = callback.data.split(":")[2]
    if not idx_str.isdigit():
        await callback.answer()
        return

    idx = int(idx_str)
    docs = await vector_db.get_sources_stats(user_id)
    if idx >= len(docs):
        await callback.answer("Файл уже удален.", show_alert=True)
        return

    filename = docs[idx]["source"]
    chunks = docs[idx]["chunk_count"]
    text = (
        f"⚠️ <b>Удалить документ «{filename}»?</b>\n\n"
        f"Будет удалено чанков из вашей базы: <b>{chunks}</b>."
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_doc_confirm_delete_kb(idx))
    await callback.answer()


@router.callback_query(F.data.startswith("user:doc_do_del:"))
async def cb_user_doc_do_del(
    callback: CallbackQuery,
    settings: Settings,
    vector_db: VectorDBService,
    db_service: DatabaseService,
) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    idx_str = callback.data.split(":")[2]
    if not idx_str.isdigit():
        await callback.answer()
        return

    idx = int(idx_str)
    docs = await vector_db.get_sources_stats(user_id)
    if idx >= len(docs):
        await callback.answer("Файл уже удален.", show_alert=True)
        return

    filename = docs[idx]["source"]
    deleted = await vector_db.delete_source(user_id, filename)
    await db_service.delete_document(user_id, filename)

    local_file = settings.DOCS_STORAGE_DIR / str(user_id) / filename
    if local_file.exists():
        try:
            local_file.unlink()
        except Exception:
            pass

    await callback.answer(f"Документ «{filename}» удален ({deleted} чанков).", show_alert=True)

    updated_docs = await vector_db.get_sources_stats(user_id)
    total_chunks = sum(d["chunk_count"] for d in updated_docs)
    text = (
        "📂 <b>Ваша персональная база знаний</b>\n\n"
        f"• Загружено документов: <b>{len(updated_docs)}</b>\n"
        f"• Всего векторизованных чанков: <b>{total_chunks}</b>\n\n"
        "<i>Вы можете удалить любой документ кнопкой ниже:</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_docs_list_kb(updated_docs))


@router.callback_query(F.data == "user:doc_clear_confirm")
async def cb_user_doc_clear_confirm(callback: CallbackQuery) -> None:
    text = (
        "💥 <b>Полная очистка вашей личной базы знаний</b>\n\n"
        "Вы действительно хотите удалить ВСЕ ваши документы из базы?\n"
        "Это действие необратимо."
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_clear_confirm_kb())
    await callback.answer()


@router.callback_query(F.data == "user:doc_do_clear")
async def cb_user_doc_do_clear(
    callback: CallbackQuery,
    vector_db: VectorDBService,
    db_service: DatabaseService,
) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    cleared = await vector_db.clear_all(user_id)
    await db_service.clear_user_documents(user_id)
    await callback.answer(f"Ваша база знаний полностью очищена ({cleared} чанков удалено).", show_alert=True)

    text = "📂 <b>Ваша персональная база знаний пуста.</b>\nОтправьте файлы в чат, чтобы наполнить ее."
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_docs_list_kb([]))


@router.callback_query(F.data == "user:close")
async def cb_user_close(callback: CallbackQuery) -> None:
    if callback.message and isinstance(callback.message, Message):
        try:
            await callback.message.delete()
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data == "user:show_menu")
async def cb_user_show_menu(callback: CallbackQuery, settings: Settings) -> None:
    """Принудительное восстановление нижнего Reply-меню."""
    user_id = callback.from_user.id if callback.from_user else 0
    is_admin = user_id in settings.ADMIN_IDS
    await callback.message.answer(
        "🔘 Меню восстановлено!",
        reply_markup=get_main_reply_kb(is_admin=is_admin),
    )
    await callback.answer()


@router.callback_query(F.data == "user:noop")
async def cb_user_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("fb:"))
async def handle_feedback(
    callback: CallbackQuery,
    db_service: DatabaseService
) -> None:
    """Обработка нажатий на inline-кнопки оценки ответа."""
    data = callback.data or ""
    if data == "fb:already_voted":
        await callback.answer("Вы уже проголосовали за этот ответ.", show_alert=False)
        return

    parts = data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    action, log_id_str = parts[1], parts[2]
    if not log_id_str.isdigit():
        await callback.answer()
        return

    log_id = int(log_id_str)
    rating = 1 if action == "like" else -1

    await db_service.set_feedback(log_id=log_id, rating=rating)

    if callback.message and isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(
                reply_markup=get_feedback_voted_keyboard(rating)
            )
        except Exception as e:
            logger.debug(f"Failed to update feedback markup: {e}")

    thanks_msg = "Спасибо за положительный отзыв! 👍" if rating == 1 else "Спасибо за отзыв, мы учтем замечания! 👎"
    await callback.answer(thanks_msg, show_alert=False)
