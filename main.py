import os
import subprocess
import sys
from pathlib import Path

# Принудительная установка рабочей директории в корень проекта
_PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(_PROJECT_ROOT)

# Принудительная установка UTF-8 для консоли Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Автоматический запуск через виртуальное окружение .venv, если скрипт вызван через глобальный Python
_venv_python = _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if _venv_python.exists() and os.environ.get("_SCHOOLX_REDIRECTED") != "1":
    try:
        _curr_python = Path(sys.executable).resolve()
        _target_python = _venv_python.resolve()
        if _curr_python != _target_python:
            env = os.environ.copy()
            env["_SCHOOLX_REDIRECTED"] = "1"
            proc = subprocess.run([str(_target_python), *sys.argv], env=env, cwd=str(_PROJECT_ROOT))
            sys.exit(proc.returncode)
    except Exception:
        pass

import asyncio
import aiohttp
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from loguru import logger

from bot.config import settings
from bot.handlers import (
    admin_billing_router,
    admin_router,
    subscription_router,
    user_router,
)
from bot.loader import (
    billing_service,
    bot,
    create_bot_session,
    db_service,
    dp,
    queue_manager,
    rag_client,
)
from bot.middlewares.subscription_check import SubscriptionMiddleware


def setup_logging() -> None:
    """Настройка логирования через loguru."""
    logger.remove()

    # Консольный вывод с цветовой дифференциацией
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
        ),
    )

    # Ротируемый файл логов
    log_path = settings.BASE_DIR / "logs" / "bot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_path),
        level="DEBUG",
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        encoding="utf-8",
    )


def acquire_single_instance_lock(lock_file_path: Path):
    """Предотвращает одновременный запуск нескольких экземпляров бота (Telegram Conflict 409)."""
    try:
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = open(lock_file_path, "a+")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (BlockingIOError, OSError, IOError):
        return None


def setup_routers_and_middlewares() -> None:
    """Регистрация middlewares и роутеров до начала polling."""
    sub_middleware = SubscriptionMiddleware(
        settings=settings,
        db_service=db_service,
        billing_service=billing_service
    )
    dp.message.middleware(sub_middleware)
    dp.callback_query.middleware(sub_middleware)

    dp.include_router(admin_router)
    dp.include_router(admin_billing_router)
    dp.include_router(subscription_router)
    dp.include_router(user_router)
    logger.info("Middlewares and routers attached to Dispatcher.")


async def register_bot_commands() -> None:
    """Регистрация нативных команд и кнопки меню Telegram с защитой от таймаута."""
    user_commands = [
        BotCommand(command="start", description="🚀 Главное меню"),
        BotCommand(command="reset", description="🔄 Новая тема (сброс контекста)"),
        BotCommand(command="new_topic", description="🔄 Начать новую тему"),
        BotCommand(command="subscribe", description="💎 Тарифы и скидки 40%"),
        BotCommand(command="my_sub", description="📋 Моя подписка"),
        BotCommand(command="my_files", description="📁 Мои документы"),
        BotCommand(command="clear_my_db", description="🗑 Очистить мои документы"),
        BotCommand(command="help", description="📖 Справка и помощь"),
    ]
    try:
        await asyncio.wait_for(bot.set_my_commands(user_commands, scope=BotCommandScopeDefault()), timeout=6)

        admin_commands = user_commands + [
            BotCommand(command="admin", description="🛠 Консоль админа"),
            BotCommand(command="admin_billing", description="💰 Биллинг и чеки"),
            BotCommand(command="stats", description="📊 Статистика системы"),
        ]
        for admin_id in settings.ADMIN_IDS:
            try:
                await asyncio.wait_for(
                    bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id)),
                    timeout=4
                )
            except Exception:
                pass
        logger.info("Telegram Bot Menu commands successfully registered.")
    except Exception as cmd_err:
        logger.warning(f"Could not register Telegram Bot Menu commands (offline/timeout): {cmd_err}")


async def main() -> None:
    """Главная точка входа приложения."""
    setup_logging()

    # Защита от запуска нескольких экземпляров (409 Conflict)
    lock_file = settings.BASE_DIR / "data" / "bot.lock"
    lock_fd = acquire_single_instance_lock(lock_file)
    if lock_fd is None:
        logger.error(
            "⚠️ Другой процесс SchoolX Bot уже запущен и удерживает блокировку (data/bot.lock). "
            "Завершение текущего процесса во избежание Telegram 409 Conflict."
        )
        return

    logger.info("Starting SchoolX RAG Telegram Bot...")

    # Валидация токена бота
    if not settings.BOT_TOKEN or "DummyBotToken" in settings.BOT_TOKEN:
        logger.critical(
            "BOT_TOKEN не задан! Пожалуйста, скопируйте .env.example в .env "
            "и укажите реальный токен Telegram бота."
        )
        sys.exit(1)

    # 1. Проверка и создание рабочих каталогов
    settings.ensure_directories()

    # 2. Инициализация базы данных SQLite и пользователей
    await db_service.init_db()
    await db_service.bootstrap_users(
        admin_ids=settings.ADMIN_IDS,
        initial_allowed_ids=settings.ALLOWED_TELEGRAM_IDS
    )

    # 3. Подключение middlewares и роутеров
    setup_routers_and_middlewares()

    # 4. Запуск фонового воркера очереди взаимодействия с RAG API
    queue_manager.start(bot)
    logger.info(
        f"SchoolX Bot thin client ready! Admins: {len(settings.ADMIN_IDS)}, "
        f"RAG Base URL: {settings.RAG_API_BASE_URL}, Stream enabled: {settings.RAG_STREAM_ENABLED}"
    )

    # Типы событий для long polling
    used_updates = dp.resolve_used_update_types()

    # Устойчивый цикл опроса Telegram с автоматическим переподключением при сбоях сети
    retry_delay = 3
    max_delay = 30
    commands_registered = False

    try:
        while True:
            try:
                # Регистрация меню Telegram при доступности сети
                if not commands_registered:
                    await register_bot_commands()
                    commands_registered = True

                # Сброс накопившихся обновлений при старте/рестарте соединения
                try:
                    await asyncio.wait_for(bot.delete_webhook(drop_pending_updates=True), timeout=6)
                except Exception as e:
                    logger.debug(f"delete_webhook note: {e}")

                logger.info(f"Connecting to Telegram long polling (allowed_updates: {used_updates})...")
                await dp.start_polling(
                    bot,
                    allowed_updates=used_updates,
                    handle_signals=False,
                    close_bot_session=False,
                )
                # Если start_polling завершился штатно без исключений
                break

            except (TelegramNetworkError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as net_err:
                logger.warning(
                    f"⚠️ Временная ошибка сети Telegram: {net_err}. "
                    f"Повторная попытка подключения через {retry_delay} сек... "
                    f"(Если Telegram заблокирован провайдером, включите VPN или укажите TELEGRAM_PROXY в .env)"
                )
                # Пересоздаем сессию bot с IPv4-коннектором
                try:
                    if bot.session and not bot.session.closed:
                        await bot.session.close()
                except Exception:
                    pass
                bot.session = create_bot_session()

                await asyncio.sleep(retry_delay)
                retry_delay = min(int(retry_delay * 1.5) + 1, max_delay)

            except TelegramConflictError as conflict_err:
                logger.error(
                    f"⚠️ Telegram 409 Conflict: {conflict_err}. "
                    f"Пауза 10 сек перед повторной попыткой..."
                )
                await asyncio.sleep(10)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
    except Exception as exc:
        logger.exception(f"Unexpected fatal error: {exc}")
    finally:
        logger.warning("Shutting down SchoolX RAG Telegram Bot...")
        await queue_manager.stop()
        await rag_client.close()
        if bot.session and not bot.session.closed:
            try:
                await bot.session.close()
            except Exception:
                pass
        if lock_fd:
            try:
                lock_fd.close()
            except Exception:
                pass
        logger.info("Shutdown completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
