import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid
import aiosqlite
from loguru import logger


def _format_dt(dt: datetime) -> str:
    """Форматирование datetime в строку SQLite ISO (YYYY-MM-DD HH:MM:SS)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(dt_str: str) -> datetime:
    """Парсинг строки времени из SQLite в datetime с UTC."""
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)


class DatabaseService:
    """Асинхронный сервис для работы с базой данных SQLite (aiosqlite).
    
    Обеспечивает персистентное хранение:
    - Пользователей и их ролей (users).
    - Белого списка пользователей (allowed_users) с in-memory кэшем.
    - Подписок и тарифов с контролем Early-Bird квоты (subscriptions).
    - Транзакций и чеков оплаты (payments).
    - Логов запросов, контекста и оценок качества (query_logs).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._allowed_cache: Set[int] = set()
        self._lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Инициализация структуры таблиц и индексов."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            # Таблица белого списка (обратная совместимость)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS allowed_users (
                    telegram_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Таблица пользователей
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    role TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Таблица подписок
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    tier TEXT NOT NULL,
                    is_early_bird INTEGER DEFAULT 0,
                    starts_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    status TEXT DEFAULT 'active'
                );
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_subs_user_id 
                ON subscriptions(user_id);
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_subs_status 
                ON subscriptions(status, expires_at);
                """
            )

            # Таблица платежей
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    tier TEXT NOT NULL,
                    payment_method TEXT DEFAULT 'manual',
                    status TEXT DEFAULT 'pending',
                    receipt_file_id TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_user_id 
                ON payments(user_id);
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_status 
                ON payments(status);
                """
            )

            # Таблица логов запросов RAG
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS query_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    query_text TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    sources TEXT,
                    rating INTEGER DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Таблица загруженных документов пользователей
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    chunks_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, filename) ON CONFLICT REPLACE
                );
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_documents_user_id 
                ON user_documents(user_id);
                """
            )
            await db.commit()

        # Загрузка разрешенных пользователей в кэш
        await self._reload_cache()
        logger.info(f"SQLite DB initialized at {self.db_path}. Loaded {len(self._allowed_cache)} allowed users.")

    async def _reload_cache(self) -> None:
        """Синхронизация in-memory кэша разрешенных пользователей."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT telegram_id FROM allowed_users;") as cursor:
                rows = await cursor.fetchall()
                self._allowed_cache = {row[0] for row in rows}

    async def bootstrap_users(self, admin_ids: Set[int], initial_allowed_ids: Set[int]) -> None:
        """Добавление системных администраторов и начального списка пользователей."""
        all_to_add = admin_ids | initial_allowed_ids
        if not all_to_add:
            return

        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                for uid in all_to_add:
                    role = "admin" if uid in admin_ids else "user"
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO allowed_users (telegram_id, added_by)
                        VALUES (?, 0);
                        """,
                        (uid,)
                    )
                    await db.execute(
                        """
                        INSERT INTO users (telegram_id, role)
                        VALUES (?, ?)
                        ON CONFLICT(telegram_id) DO UPDATE SET role=excluded.role;
                        """,
                        (uid, role)
                    )
                await db.commit()
            await self._reload_cache()
            logger.info(f"Bootstrapped {len(all_to_add)} users into whitelist & users table.")

    # ==============================================================================
    # Работа с пользователями
    # ==============================================================================

    async def get_or_create_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        role: str = "user"
    ) -> Dict[str, Any]:
        """Получение или создание профиля пользователя."""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM users WHERE telegram_id = ?;", (telegram_id,)) as cur:
                    row = await cur.fetchone()
                    if row:
                        user_dict = dict(row)
                        if username and user_dict.get("username") != username:
                            await db.execute(
                                "UPDATE users SET username = ? WHERE telegram_id = ?;",
                                (username, telegram_id)
                            )
                            await db.commit()
                            user_dict["username"] = username
                        return user_dict

                now_str = _format_dt(datetime.now(timezone.utc))
                await db.execute(
                    """
                    INSERT INTO users (telegram_id, username, role, created_at)
                    VALUES (?, ?, ?, ?);
                    """,
                    (telegram_id, username or "", role, now_str)
                )
                await db.commit()
                return {
                    "telegram_id": telegram_id,
                    "username": username or "",
                    "role": role,
                    "created_at": now_str
                }

    async def is_user_allowed(self, user_id: int) -> bool:
        """Проверка наличия пользователя в белом списке (через in-memory кэш)."""
        return user_id in self._allowed_cache

    async def add_user(self, user_id: int, added_by: int) -> bool:
        """Добавление пользователя в белый список."""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO allowed_users (telegram_id, added_by)
                    VALUES (?, ?);
                    """,
                    (user_id, added_by)
                )
                await db.commit()
            self._allowed_cache.add(user_id)
            logger.info(f"User {user_id} added to whitelist by {added_by}.")
            return True

    async def ban_user(self, user_id: int) -> bool:
        """Удаление пользователя из белого списка и отмена активных подписок."""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM allowed_users WHERE telegram_id = ?;",
                    (user_id,)
                )
                # Помечаем активные подписки как canceled
                await db.execute(
                    "UPDATE subscriptions SET status = 'canceled' WHERE user_id = ? AND status = 'active';",
                    (user_id,)
                )
                await db.commit()
                deleted = cursor.rowcount > 0

            self._allowed_cache.discard(user_id)
            if deleted:
                logger.info(f"User {user_id} removed from whitelist.")
            return deleted

    async def get_all_allowed_users(self) -> List[int]:
        """Получение списка ID всех разрешенных пользователей."""
        return sorted(list(self._allowed_cache))

    # ==============================================================================
    # Подписки и динамическое ценообразование (Early-Bird)
    # ==============================================================================

    async def get_early_bird_count(self) -> int:
        """Подсчет количества пользователей с активной подпиской по тарифу Early-Bird."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(DISTINCT user_id) 
                FROM subscriptions 
                WHERE is_early_bird = 1 
                  AND status = 'active' 
                  AND expires_at > datetime('now');
                """
            ) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0

    async def get_active_subscription(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение активной подписки пользователя с расчетом оставшихся дней."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, user_id, tier, is_early_bird, starts_at, expires_at, status
                FROM subscriptions
                WHERE user_id = ? 
                  AND status = 'active' 
                  AND expires_at > datetime('now')
                ORDER BY expires_at DESC 
                LIMIT 1;
                """,
                (user_id,)
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                try:
                    exp_dt = _parse_dt(data["expires_at"])
                    now = datetime.now(timezone.utc)
                    diff = exp_dt - now
                    # Округление до целых дней вверх
                    seconds_left = max(0, int(diff.total_seconds()))
                    days_left = max(1 if seconds_left > 0 else 0, (seconds_left + 86399) // 86400)
                    data["days_remaining"] = days_left
                except Exception:
                    data["days_remaining"] = 0
                return data

    async def is_subscription_active(self, user_id: int) -> bool:
        """Проверка активности подписки пользователя."""
        sub = await self.get_active_subscription(user_id)
        return sub is not None

    # ==============================================================================
    # Платежи и заказы
    # ==============================================================================

    async def create_payment(
        self,
        user_id: int,
        amount: int,
        tier: str,
        payment_method: str = "manual",
        receipt_file_id: Optional[str] = None,
        payment_id: Optional[str] = None
    ) -> str:
        """Создание новой записи о платеже со статусом pending."""
        pid = payment_id or uuid.uuid4().hex[:12]
        now_str = _format_dt(datetime.now(timezone.utc))
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO payments (id, user_id, amount, tier, payment_method, status, receipt_file_id, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?);
                    """,
                    (pid, user_id, amount, tier, payment_method, receipt_file_id, now_str)
                )
                await db.commit()
        logger.info(f"Created pending payment {pid} for user {user_id}: {amount} RUB, tier={tier}, method={payment_method}")
        return pid

    async def get_payment(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Получение данных платежа по ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM payments WHERE id = ?;", (payment_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def confirm_payment(
        self,
        payment_id: str,
        admin_id: Optional[int] = None,
        early_bird_limit: int = 10,
        subscription_days: int = 30
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Атомарное подтверждение платежа с фиксацией квоты Early-Bird и активацией подписки.
        
        Исключает race condition благодаря self._lock и транзакции SQLite.
        """
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM payments WHERE id = ?;", (payment_id,)) as cur:
                    p_row = await cur.fetchone()
                    if not p_row:
                        return False, "Платеж не найден.", None
                    payment = dict(p_row)

                if payment["status"] != "pending":
                    return False, f"Платеж уже обработан (статус: {payment['status']}).", payment

                user_id = payment["user_id"]
                tier = payment["tier"]

                # Проверка Early-Bird квоты
                async with db.execute(
                    """
                    SELECT 1 FROM subscriptions 
                    WHERE user_id = ? AND is_early_bird = 1 AND status = 'active' AND expires_at > datetime('now')
                    LIMIT 1;
                    """,
                    (user_id,)
                ) as cur:
                    is_already_early = (await cur.fetchone()) is not None

                async with db.execute(
                    """
                    SELECT COUNT(DISTINCT user_id) 
                    FROM subscriptions 
                    WHERE is_early_bird = 1 AND status = 'active' AND expires_at > datetime('now');
                    """
                ) as cur:
                    current_early_count = (await cur.fetchone())[0]

                # Первые 10 клиентов получают статус Early-Bird
                is_early_bird = 1 if (is_already_early or current_early_count < early_bird_limit) else 0

                now = datetime.now(timezone.utc)
                # Проверка наличия действующей подписки для корректного продления
                async with db.execute(
                    """
                    SELECT expires_at FROM subscriptions
                    WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')
                    ORDER BY expires_at DESC LIMIT 1;
                    """,
                    (user_id,)
                ) as cur:
                    existing_sub = await cur.fetchone()

                if existing_sub:
                    base_dt = _parse_dt(existing_sub["expires_at"])
                    starts_at = now
                    expires_at = base_dt + timedelta(days=subscription_days)
                else:
                    starts_at = now
                    expires_at = now + timedelta(days=subscription_days)

                starts_str = _format_dt(starts_at)
                expires_str = _format_dt(expires_at)

                # Создание записи подписки
                cursor = await db.execute(
                    """
                    INSERT INTO subscriptions (user_id, tier, is_early_bird, starts_at, expires_at, status)
                    VALUES (?, ?, ?, ?, ?, 'active');
                    """,
                    (user_id, tier, is_early_bird, starts_str, expires_str)
                )
                sub_id = cursor.lastrowid

                # Обновление статуса платежа
                await db.execute(
                    "UPDATE payments SET status = 'succeeded' WHERE id = ?;",
                    (payment_id,)
                )

                # Добавление в белый список доступа
                await db.execute(
                    "INSERT OR IGNORE INTO allowed_users (telegram_id, added_by) VALUES (?, ?);",
                    (user_id, admin_id or 0)
                )
                await db.commit()

            self._allowed_cache.add(user_id)

            sub_info = {
                "sub_id": sub_id,
                "user_id": user_id,
                "tier": tier,
                "is_early_bird": bool(is_early_bird),
                "starts_at": starts_str,
                "expires_at": expires_str,
                "days": subscription_days,
                "amount": payment["amount"],
                "payment_id": payment_id
            }
            logger.info(
                f"Payment {payment_id} confirmed. Sub {sub_id} activated for user {user_id} "
                f"until {expires_str} (early_bird={is_early_bird})."
            )
            return True, "Подписка успешно активирована!", sub_info

    async def reject_payment(
        self,
        payment_id: str,
        admin_id: Optional[int] = None,
        reason: Optional[str] = None
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Отклонение платежа администратором."""
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM payments WHERE id = ?;", (payment_id,)) as cur:
                    p_row = await cur.fetchone()
                    if not p_row:
                        return False, "Платеж не найден.", None
                    payment = dict(p_row)

                if payment["status"] != "pending":
                    return False, f"Платеж уже обработан (статус: {payment['status']}).", payment

                await db.execute(
                    "UPDATE payments SET status = 'rejected' WHERE id = ?;",
                    (payment_id,)
                )
                await db.commit()

            logger.info(f"Payment {payment_id} rejected by admin {admin_id}. Reason: {reason}")
            return True, "Платеж отклонен.", payment

    async def grant_subscription(
        self,
        user_id: int,
        days: int,
        tier: str = "basic",
        is_early_bird: int = 0,
        admin_id: int = 0
    ) -> Dict[str, Any]:
        """Ручное предоставление или продление подписки администратором."""
        now = datetime.now(timezone.utc)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT expires_at FROM subscriptions
                    WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')
                    ORDER BY expires_at DESC LIMIT 1;
                    """,
                    (user_id,)
                ) as cur:
                    existing_sub = await cur.fetchone()

                if existing_sub:
                    base_dt = _parse_dt(existing_sub["expires_at"])
                    starts_at = now
                    expires_at = base_dt + timedelta(days=days)
                else:
                    starts_at = now
                    expires_at = now + timedelta(days=days)

                starts_str = _format_dt(starts_at)
                expires_str = _format_dt(expires_at)

                cursor = await db.execute(
                    """
                    INSERT INTO subscriptions (user_id, tier, is_early_bird, starts_at, expires_at, status)
                    VALUES (?, ?, ?, ?, ?, 'active');
                    """,
                    (user_id, tier, is_early_bird, starts_str, expires_str)
                )
                sub_id = cursor.lastrowid

                # Логирование транзакции
                grant_pid = f"grant_{uuid.uuid4().hex[:8]}"
                await db.execute(
                    """
                    INSERT INTO payments (id, user_id, amount, tier, payment_method, status, created_at)
                    VALUES (?, ?, 0, ?, 'admin_grant', 'succeeded', ?);
                    """,
                    (grant_pid, user_id, tier, _format_dt(now))
                )

                # Белый список
                await db.execute(
                    "INSERT OR IGNORE INTO allowed_users (telegram_id, added_by) VALUES (?, ?);",
                    (user_id, admin_id)
                )
                await db.commit()

            self._allowed_cache.add(user_id)

        logger.info(f"Admin {admin_id} granted {days} days of tier '{tier}' to user {user_id} (until {expires_str}).")
        return {
            "sub_id": sub_id,
            "user_id": user_id,
            "tier": tier,
            "is_early_bird": bool(is_early_bird),
            "starts_at": starts_str,
            "expires_at": expires_str,
            "days": days
        }

    async def get_billing_stats(self, early_bird_limit: int = 10) -> Dict[str, Any]:
        """Сводная статистика биллинга: активные подписки, Early-Bird места, выручка."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(DISTINCT user_id) 
                FROM subscriptions 
                WHERE status = 'active' AND expires_at > datetime('now');
                """
            ) as cur:
                active_subs = (await cur.fetchone())[0]

            async with db.execute(
                """
                SELECT COUNT(DISTINCT user_id) 
                FROM subscriptions 
                WHERE is_early_bird = 1 AND status = 'active' AND expires_at > datetime('now');
                """
            ) as cur:
                early_bird_count = (await cur.fetchone())[0]

            async with db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'succeeded';"
            ) as cur:
                total_revenue = (await cur.fetchone())[0]

            async with db.execute(
                "SELECT COUNT(*) FROM payments WHERE status = 'pending';"
            ) as cur:
                pending_count = (await cur.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM subscriptions;") as cur:
                total_subs = (await cur.fetchone())[0]

        return {
            "active_subscriptions": active_subs,
            "total_subscriptions": total_subs,
            "early_bird_count": early_bird_count,
            "early_bird_limit": early_bird_limit,
            "early_bird_remaining": max(0, early_bird_limit - early_bird_count),
            "total_revenue": total_revenue,
            "pending_payments_count": pending_count,
        }

    async def get_pending_payments(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Получение списка ожидающих подтверждения платежей."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM payments 
                WHERE status = 'pending' 
                ORDER BY created_at ASC 
                LIMIT ?;
                """,
                (limit,)
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    # ==============================================================================
    # Логи запросов и обратная связь (RAG)
    # ==============================================================================

    async def log_query(
        self,
        user_id: int,
        query_text: str,
        response_text: str,
        sources: Optional[str] = None
    ) -> int:
        """Логирование выполненного RAG-запроса и возврат ID записи для фидбека."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO query_logs (user_id, query_text, response_text, sources)
                VALUES (?, ?, ?, ?);
                """,
                (user_id, query_text, response_text, sources or "")
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def set_feedback(self, log_id: int, rating: int) -> bool:
        """Сохранение оценки пользователя (1 = Like, -1 = Dislike)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE query_logs SET rating = ? WHERE id = ?;",
                (rating, log_id)
            )
            await db.commit()
            updated = cursor.rowcount > 0
            if updated:
                logger.info(f"Feedback updated for log_id={log_id}: rating={rating}")
            return updated

    # ==============================================================================
    # Управление документами пользователей (SQLite)
    # ==============================================================================

    async def record_document(
        self,
        user_id: int,
        filename: str,
        file_type: str,
        file_size: int,
        chunks_count: int
    ) -> int:
        """Сохранение или обновление метаданных документа пользователя в SQLite."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO user_documents (user_id, filename, file_type, file_size, chunks_count, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, filename) DO UPDATE SET
                    file_type = excluded.file_type,
                    file_size = excluded.file_size,
                    chunks_count = excluded.chunks_count,
                    created_at = CURRENT_TIMESTAMP;
                """,
                (user_id, filename, file_type.lower(), file_size, chunks_count)
            )
            await db.commit()
            logger.info(f"SQLite: recorded document '{filename}' for user {user_id} ({chunks_count} chunks, {file_size} bytes).")
            return cursor.lastrowid or 0

    async def delete_document(self, user_id: int, filename: str) -> bool:
        """Удаление метаданных документа пользователя из SQLite."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM user_documents WHERE user_id = ? AND filename = ?;",
                (user_id, filename)
            )
            await db.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"SQLite: deleted document record '{filename}' for user {user_id}.")
            return deleted

    async def clear_user_documents(self, user_id: int) -> int:
        """Удаление всех документов пользователя из SQLite."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM user_documents WHERE user_id = ?;",
                (user_id,)
            )
            await db.commit()
            deleted = cursor.rowcount
            logger.info(f"SQLite: cleared all documents for user {user_id} ({deleted} records).")
            return deleted

    async def get_user_documents(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение списка загруженных документов пользователя из SQLite."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, user_id, filename, file_type, file_size, chunks_count, created_at
                FROM user_documents
                WHERE user_id = ?
                ORDER BY created_at DESC;
                """,
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def get_total_documents_count(self) -> int:
        """Общее количество сохраненных документов в системе."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM user_documents;") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_stats(self) -> Dict[str, Any]:
        """Получение сводной статистики использования бота."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM allowed_users;") as cursor:
                total_users = (await cursor.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM query_logs;") as cursor:
                total_queries = (await cursor.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM query_logs WHERE rating = 1;") as cursor:
                positive_feedback = (await cursor.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM query_logs WHERE rating = -1;") as cursor:
                negative_feedback = (await cursor.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM user_documents;") as cursor:
                total_docs = (await cursor.fetchone())[0]

        return {
            "total_allowed_users": total_users,
            "total_queries": total_queries,
            "positive_feedback": positive_feedback,
            "negative_feedback": negative_feedback,
            "total_documents": total_docs,
        }
