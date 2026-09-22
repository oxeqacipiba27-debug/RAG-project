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
    BTN_RESET_SESSION,
    get_main_reply_kb,
    get_start_inline_kb,
)
from bot.keyboards.user_kb import (
    get_user_clear_confirm_kb,
    get_user_doc_confirm_delete_kb,
    get_user_docs_list_kb,
)
from bot.services.db_service import DatabaseService
from bot.services.queue_manager import QueueManager
from bot.services.rag_client import RAGApiClient
from bot.utils.formatter import split_message_text, strip_html_tags

router = Router(name="user_router")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    settings: Settings
) -> None:
    """Приветственное сообщение, установка нижнего меню и быстрые кнопки."""
    user_id = message.from_user.id if message.from_user else 0
    is_admin = user_id in settings.ADMIN_IDS

    welcome_text = (
        "👋 <b>Добро пожаловать в интеллектуальную систему SchoolX!</b>\n\n"
        "Я — ваш персональный ИИ-консультант по нормативным документам, регламентам и внутренним актам.\n\n"
        "💡 <b>Как пользоваться:</b>\n"
        "• <b>Задавайте вопросы:</b> Напишите любой интересующий вас вопрос обычным текстом. "
        "Ответ будет сформирован строго на основе проверенных документов с указанием источников и страниц.\n"
        "• <b>Новая тема диалога:</b> Используйте кнопку «🔄 Новая тема» или команду /reset, чтобы сбросить контекст беседы.\n"
        "• <b>Справка:</b> Нажмите «📖 Справка» или введите /help для получения дополнительной информации.\n\n"
        "<i>Задайте ваш первый вопрос прямо в этот чат!</i>"
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


@router.message(or_f(Command("reset"), Command("new_topic"), F.text.in_([BTN_RESET_SESSION, "🔄 Новая тема", "Сбросить контекст"])))
async def cmd_reset_topic(
    message: Message,
    rag_client: RAGApiClient
) -> None:
    """Сброс контекста беседы на стороне RAG-приложения."""
    chat_id = message.chat.id
    try:
        ok = await rag_client.reset_session(chat_id)
        if ok:
            await message.answer(
                "🔄 <b>Контекст беседы сброшен.</b>\n\n"
                "История предыдущих сообщений очищена на сервере базы знаний. "
                "Задайте новый вопрос, и мы начнем новую тему диалога!"
            )
        else:
            await message.answer(
                "⚠️ Не удалось сбросить контекст беседы на сервере. Пожалуйста, повторите попытку."
            )
    except Exception as e:
        logger.error(f"Error resetting session for chat {chat_id}: {e}")
        await message.answer(
            "❌ <b>Сервис базы знаний временно недоступен.</b>\n"
            "Пожалуйста, повторите попытку сброса позже."
        )


@router.message(or_f(Command("help"), F.text.in_([BTN_HELP, "Справка", "Помощь"])))
async def cmd_help(message: Message) -> None:
    """Справочная информация."""
    help_text = (
        "📖 <b>Справка по работе с базой знаний:</b>\n\n"
        "1. <b>Как задать вопрос:</b>\n"
        "Отправьте вопрос текстом в чат. Система автоматически найдет релевантные разделы в нормативных документах, "
        "сформирует точный структурированный ответ и приведет ссылки на первоисточники (документ и страницу).\n\n"
        "2. <b>Управление контекстом и сессиями:</b>\n"
        "Бот помнит контекст предыдущих реплик. Если вы хотите переключиться на совершенно другую тему, "
        "нажмите кнопку «🔄 Новая тема» или отправьте команду /reset (/new_topic).\n\n"
        "3. <b>Подписка и тарифы:</b>\n"
        "• Кнопка «💎 Тарифы и подписка» — витрина тарифов;\n"
        "• Кнопка «📋 Моя подписка» — статус вашей текущей подписки.\n\n"
        "4. <b>Очередь и генерация:</b>\n"
        "Для стабильности работы инференс выполняется последовательно. При высокой нагрузке бот сообщит вашу позицию в очереди."
    )
    await message.answer(help_text)


@router.message(or_f(Command("my_files"), F.text.in_([BTN_MY_FILES, "Мои файлы", "Мои документы"])))
async def cmd_my_files(message: Message, db_service: DatabaseService) -> None:
    """Просмотр и управление личными документами пользователя."""
    user_id = message.from_user.id if message.from_user else 0
    user_docs = await db_service.get_user_documents(user_id)
    docs = [{"source": d["filename"], "chunk_count": d.get("chunks_count", 0)} for d in user_docs]
    total_docs = len(docs)

    text = (
        "📂 <b>Ваши сохраненные документы</b>\n\n"
        f"• Загружено документов: <b>{total_docs}</b>\n\n"
        "<i>Вы можете удалить любой документ кнопкой ниже:</i>"
    )
    await message.answer(text, reply_markup=get_user_docs_list_kb(docs))


@router.message(or_f(Command("clear_my_db"), F.text.in_([BTN_CLEAR_DB, "Очистить базу"])))
async def cmd_clear_my_db(message: Message) -> None:
    """Запрос на полную очистку персональной базы."""
    text = (
        "⚠️ <b>Очистка сохраненных документов</b>\n\n"
        "Вы уверены, что хотите удалить ВСЕ свои сохраненные документы? "
        "Это действие необратимо."
    )
    await message.answer(text, reply_markup=get_user_clear_confirm_kb())


# ==============================================================================
# Загрузка личных документов пользователя
# ==============================================================================

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


@router.message(F.document)
async def handle_user_document_upload(
    message: Message,
    bot: Bot,
    settings: Settings,
    db_service: DatabaseService
) -> None:
    """Прием документов в хранилище без локального инференса."""
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
        f"⏳ <b>Документ «{filename}» получен.</b> Сохраняю в хранилище..."
    )

    # Изолированная директория для пользователя
    user_docs_dir = settings.DOCS_STORAGE_DIR / str(user_id)
    user_docs_dir.mkdir(parents=True, exist_ok=True)
    local_path = user_docs_dir / filename

    try:
        await bot.download(doc, destination=local_path)
        file_size = local_path.stat().st_size
        await db_service.record_document(
            user_id=user_id,
            filename=filename,
            file_type=file_ext,
            file_size=file_size,
            chunks_count=0
        )
        await status_msg.edit_text(
            f"✅ <b>Документ «{filename}» успешно сохранен в хранилище!</b>\n\n"
            f"• Размер: <b>{file_size / 1024:.1f} КБ</b>\n"
            f"• Файл добавлен в очередь индексации базы знаний.\n\n"
            "Вы можете задавать вопросы по базе знаний в любое время!"
        )
        logger.info(f"User {user_id}: Downloaded and recorded '{filename}'.")

    except Exception as e:
        logger.exception(f"Error during user document upload for '{filename}': {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка при сохранении документа:</b>\n<code>{e}</code>"
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
        answer, log_id, status_flag = await queue_manager.submit_query(
            user_id=user_id,
            chat_id=chat_id,
            query=query,
            bot=bot
        )

        # Если ответ не был отправлен напрямую через стриминг
        if status_flag == "NEEDS_SEND":
            chunks = split_message_text(answer, max_length=4000)
            for i, chunk in enumerate(chunks):
                is_last = (i == len(chunks) - 1)
                kb = get_feedback_keyboard(log_id) if (is_last and log_id > 0) else None
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
            "Пожалуйста, попробуйте снова через пару минут или обратитесь к администратору."
        )


# ==============================================================================
# Callback-обработчики пользователя
# ==============================================================================

@router.callback_query(F.data == "user:docs:list")
async def cb_user_docs_list(callback: CallbackQuery, db_service: DatabaseService) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    user_docs = await db_service.get_user_documents(user_id)
    docs = [{"source": d["filename"], "chunk_count": d.get("chunks_count", 0)} for d in user_docs]

    text = (
        "📂 <b>Ваши сохраненные документы</b>\n\n"
        f"• Загружено документов: <b>{len(docs)}</b>\n\n"
        "<i>Вы можете удалить любой документ кнопкой ниже:</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_docs_list_kb(docs))
    await callback.answer()


@router.callback_query(F.data.startswith("user:doc_del:"))
async def cb_user_doc_del(callback: CallbackQuery, db_service: DatabaseService) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    idx_str = callback.data.split(":")[2]
    if not idx_str.isdigit():
        await callback.answer()
        return

    idx = int(idx_str)
    user_docs = await db_service.get_user_documents(user_id)
    docs = [{"source": d["filename"], "chunk_count": d.get("chunks_count", 0)} for d in user_docs]
    if idx >= len(docs):
        await callback.answer("Файл уже удален.", show_alert=True)
        return

    filename = docs[idx]["source"]
    text = f"⚠️ <b>Удалить документ «{filename}»?</b>"
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_doc_confirm_delete_kb(idx))
    await callback.answer()


@router.callback_query(F.data.startswith("user:doc_do_del:"))
async def cb_user_doc_do_del(
    callback: CallbackQuery,
    settings: Settings,
    db_service: DatabaseService,
) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    idx_str = callback.data.split(":")[2]
    if not idx_str.isdigit():
        await callback.answer()
        return

    idx = int(idx_str)
    user_docs = await db_service.get_user_documents(user_id)
    docs = [{"source": d["filename"], "chunk_count": d.get("chunks_count", 0)} for d in user_docs]
    if idx >= len(docs):
        await callback.answer("Файл уже удален.", show_alert=True)
        return

    filename = docs[idx]["source"]
    await db_service.delete_document(user_id, filename)

    local_file = settings.DOCS_STORAGE_DIR / str(user_id) / filename
    if local_file.exists():
        try:
            local_file.unlink()
        except Exception:
            pass

    await callback.answer(f"Документ «{filename}» удален.", show_alert=True)

    updated_user_docs = await db_service.get_user_documents(user_id)
    updated_docs = [{"source": d["filename"], "chunk_count": d.get("chunks_count", 0)} for d in updated_user_docs]
    text = (
        "📂 <b>Ваши сохраненные документы</b>\n\n"
        f"• Загружено документов: <b>{len(updated_docs)}</b>\n\n"
        "<i>Вы можете удалить любой документ кнопкой ниже:</i>"
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_docs_list_kb(updated_docs))


@router.callback_query(F.data == "user:doc_clear_confirm")
async def cb_user_doc_clear_confirm(callback: CallbackQuery) -> None:
    text = (
        "💥 <b>Полная очистка ваших документов</b>\n\n"
        "Вы действительно хотите удалить ВСЕ ваши документы?\n"
        "Это действие необратимо."
    )
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=get_user_clear_confirm_kb())
    await callback.answer()


@router.callback_query(F.data == "user:doc_do_clear")
async def cb_user_doc_do_clear(
    callback: CallbackQuery,
    db_service: DatabaseService,
) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    cleared = await db_service.clear_user_documents(user_id)
    await callback.answer(f"Ваши документы удалены ({cleared} файлов).", show_alert=True)

    text = "📂 <b>У вас нет сохраненных документов.</b>"
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
