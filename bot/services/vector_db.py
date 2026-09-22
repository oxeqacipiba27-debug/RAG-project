import asyncio
import os
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional
import chromadb
from fastembed import TextEmbedding
from loguru import logger

warnings.filterwarnings("ignore", category=UserWarning, module="fastembed")
warnings.filterwarnings("ignore", category=UserWarning, module="huggingface_hub")


class VectorDBService:
    """Векторное хранилище на базе ChromaDB PersistentClient и FastEmbed (CPU).
    
    MULTI-TENANT АРХИТЕКТУРА:
    К каждому Telegram ID пользователя привязана отдельная изолированная векторная коллекция:
    `user_{user_id}`. Запросы, индексация и удаление изолированы в рамках этой коллекции.
    
    Эмбеддинги рассчитываются строго на CPU (FastEmbed singleton), что сохраняет 0% VRAM GPU.
    Все синхронные вычисления вынесены в asyncio.to_thread.
    """

    def __init__(self, persist_dir: Path, model_name: str):
        self.persist_dir = persist_dir
        self.model_name = model_name
        self.client: Optional[chromadb.PersistentClient] = None
        self._embed_model: Optional[TextEmbedding] = None
        self._lock = asyncio.Lock()

    def _sync_init(self) -> None:
        """Синхронная инициализация клиента ChromaDB и FastEmbed."""
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))
        
        num_threads = min(4, os.cpu_count() or 1)
        self._embed_model = TextEmbedding(
            model_name=self.model_name,
            threads=num_threads
        )
        logger.info(
            f"Multi-tenant VectorDB initialized at '{self.persist_dir}'. "
            f"FastEmbed model '{self.model_name}' loaded on CPU (threads={num_threads})."
        )

    async def initialize(self) -> None:
        """Асинхронная инициализация в фоновом потоке."""
        await asyncio.to_thread(self._sync_init)

    def _collection_name(self, user_id: int) -> str:
        """Имя персональной коллекции ChromaDB для пользователя."""
        return f"user_{user_id}"

    def _get_collection(self, user_id: int) -> chromadb.Collection:
        """Получение или создание персональной коллекции пользователя."""
        if not self.client:
            raise RuntimeError("Chroma client is not initialized.")
        name = self._collection_name(user_id)
        return self.client.get_or_create_collection(
            name=name,
            metadata={"user_id": str(user_id), "description": f"Personal Knowledge Base for {user_id}"}
        )

    def _sync_embed(self, texts: List[str]) -> List[List[float]]:
        """Синхронный расчет векторных представлений на CPU."""
        if not self._embed_model:
            raise RuntimeError("FastEmbed model is not initialized.")
        embeddings_gen = self._embed_model.embed(texts)
        return [emb.tolist() for emb in embeddings_gen]

    def _sync_add(
        self,
        user_id: int,
        documents: List[str],
        metadatas: List[Dict[str, Any]],
        ids: List[str]
    ) -> int:
        """Синхронное добавление чанков в персональную коллекцию пользователя."""
        col = self._get_collection(user_id)
        embeddings = self._sync_embed(documents)
        col.add(
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids
        )
        return len(documents)

    async def add_documents(
        self,
        user_id: int,
        documents: List[str],
        metadatas: List[Dict[str, Any]],
        ids: Optional[List[str]] = None
    ) -> int:
        """Асинхронное добавление чанков в персональную базу знаний пользователя."""
        if not documents:
            return 0

        if ids is None:
            ids = [f"{user_id}_{uuid.uuid4()}" for _ in documents]

        clean_metadatas = []
        for meta in metadatas:
            clean_meta = {}
            for k, v in meta.items():
                if isinstance(v, (str, int, float, bool)):
                    clean_meta[k] = v
                else:
                    clean_meta[k] = str(v)
            clean_meta["user_id"] = user_id
            clean_metadatas.append(clean_meta)

        async with self._lock:
            added_count = await asyncio.to_thread(
                self._sync_add,
                user_id,
                documents,
                clean_metadatas,
                ids
            )
            logger.info(f"Added {added_count} chunks to collection '{self._collection_name(user_id)}'.")
            return added_count

    def _sync_query(self, user_id: int, query: str, k: int) -> List[Dict[str, Any]]:
        """Синхронный поиск по сходству в персональной коллекции пользователя."""
        col = self._get_collection(user_id)
        if col.count() == 0:
            return []

        actual_k = min(k, col.count())
        query_embedding = self._sync_embed([query])[0]
        results = col.query(
            query_embeddings=[query_embedding],
            n_results=actual_k,
            include=["documents", "metadatas", "distances"]
        )

        chunks: List[Dict[str, Any]] = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for doc, meta, dist in zip(docs, metas, distances):
            chunks.append({
                "content": doc,
                "metadata": meta,
                "distance": dist
            })
        return chunks

    async def similarity_search(self, user_id: int, query: str, k: int = 4) -> List[Dict[str, Any]]:
        """Асинхронный поиск k наиболее релевантных фрагментов в базе пользователя."""
        if not query.strip():
            return []
        
        user_count = await self.count(user_id)
        if user_count == 0:
            return []

        return await asyncio.to_thread(self._sync_query, user_id, query, k)

    def _sync_count(self, user_id: int) -> int:
        col = self._get_collection(user_id)
        return col.count()

    async def count(self, user_id: int) -> int:
        """Получение количества чанков в персональной коллекции пользователя."""
        return await asyncio.to_thread(self._sync_count, user_id)

    def _sync_get_sources(self, user_id: int) -> List[str]:
        col = self._get_collection(user_id)
        if col.count() == 0:
            return []
        data = col.get(include=["metadatas"])
        metas = data.get("metadatas") or []
        sources = {m.get("source") for m in metas if m and "source" in m}
        return sorted([s for s in sources if s])

    async def get_unique_sources(self, user_id: int) -> List[str]:
        """Получение списка уникальных документов пользователя."""
        return await asyncio.to_thread(self._sync_get_sources, user_id)

    def _sync_get_sources_stats(self, user_id: int) -> List[Dict[str, Any]]:
        col = self._get_collection(user_id)
        if col.count() == 0:
            return []
        data = col.get(include=["metadatas"])
        metas = data.get("metadatas") or []
        counts: Dict[str, int] = {}
        for m in metas:
            if m and "source" in m:
                src = str(m["source"])
                counts[src] = counts.get(src, 0) + 1

        return [
            {"source": src, "chunk_count": count}
            for src, count in sorted(counts.items())
        ]

    async def get_sources_stats(self, user_id: int) -> List[Dict[str, Any]]:
        """Детальная статистика документов пользователя с подсчетом чанков."""
        return await asyncio.to_thread(self._sync_get_sources_stats, user_id)

    def _sync_delete_source(self, user_id: int, source: str) -> int:
        col = self._get_collection(user_id)
        matching = col.get(where={"source": source})
        ids_to_delete = matching.get("ids", [])
        if ids_to_delete:
            col.delete(ids=ids_to_delete)
        return len(ids_to_delete)

    async def delete_source(self, user_id: int, source: str) -> int:
        """Удаление документа из персональной базы пользователя."""
        async with self._lock:
            deleted = await asyncio.to_thread(self._sync_delete_source, user_id, source)
            logger.info(f"User {user_id}: deleted source '{source}' ({deleted} chunks removed).")
            return deleted

    def _sync_clear_all(self, user_id: int) -> int:
        if not self.client:
            return 0
        name = self._collection_name(user_id)
        col = self._get_collection(user_id)
        total = col.count()
        try:
            self.client.delete_collection(name=name)
        except Exception as e:
            logger.debug(f"Error deleting collection {name}: {e}")
        # Пересоздаем пустую коллекцию
        self._get_collection(user_id)
        return total

    async def clear_all(self, user_id: int) -> int:
        """Полная очистка персональной базы знаний пользователя."""
        async with self._lock:
            total = await asyncio.to_thread(self._sync_clear_all, user_id)
            logger.warning(f"User {user_id}: cleared entire personal collection ({total} chunks deleted).")
            return total

    def _sync_get_system_overview(self) -> Dict[str, Any]:
        """Сводная информация по всем пользовательским коллекциям для администратора."""
        if not self.client:
            return {"total_collections": 0, "total_chunks": 0, "users": []}

        collections = self.client.list_collections()
        total_chunks = 0
        users_stats = []

        for col in collections:
            cnt = col.count()
            total_chunks += cnt
            uid_str = col.name.replace("user_", "")
            uid = int(uid_str) if uid_str.isdigit() else col.name
            users_stats.append({"user_id": uid, "chunks": cnt})

        return {
            "total_collections": len(collections),
            "total_chunks": total_chunks,
            "users": users_stats
        }

    async def get_system_overview(self) -> Dict[str, Any]:
        """Асинхронная сводка по всем пользовательским базам."""
        return await asyncio.to_thread(self._sync_get_system_overview)
