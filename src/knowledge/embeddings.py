"""嵌入与向量检索模块

分块 → 调嵌入 API → 存 chunks 表 → 向量检索
"""

import asyncio
import struct
from typing import List, Optional, Dict, Any

import httpx

from ..config import get_config
from ..database import Database


class EmbeddingClient:
    """嵌入模型客户端"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        config = get_config().embedding
        self.base_url = (base_url or config.base_url).rstrip('/')
        self.api_key = api_key or config.api_key
        self.model = model or config.model
        self.dimensions = config.dimensions
        self.chunk_size = config.chunk_size
        self.chunk_overlap = config.chunk_overlap

    def chunk_text(self, text: str) -> List[str]:
        """将文本分块

        按标题层级 + 定长混合策略（约 chunk_size tokens/块）
        简化实现：按字符近似 token（中文 ~1.5字/token）
        """
        # 简化：每块约 chunk_size * 1.5 个字符
        max_chars = int(self.chunk_size * 1.5)
        overlap_chars = int(self.chunk_overlap * 1.5)

        if len(text) <= max_chars:
            return [text] if text.strip() else []

        chunks = []
        start = 0
        while start < len(text):
            end = start + max_chars
            if end >= len(text):
                chunks.append(text[start:])
                break

            # 尝试在句子边界处断开
            boundary = text.rfind('。', start, end)
            if boundary == -1 or boundary <= start:
                boundary = text.rfind('\n', start, end)
            if boundary == -1 or boundary <= start:
                boundary = end
            else:
                boundary += 1

            chunks.append(text[start:boundary])
            start = boundary - overlap_chars if boundary - overlap_chars > start else boundary

        return [c for c in chunks if c.strip()]

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量嵌入文本"""
        if not self.api_key:
            raise ValueError("Embedding API Key 未配置")

        endpoint = f"{self.base_url}/embeddings"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                endpoint,
                json={
                    "model": self.model,
                    "input": texts,
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
            )

        if response.status_code != 200:
            raise RuntimeError(f"嵌入请求失败: {response.status_code} {response.text[:200]}")

        data = response.json()
        embeddings = [item["embedding"] for item in data["data"]]
        return embeddings

    async def embed_and_store(self, item_id: int, text: str) -> int:
        """分块、嵌入、存入数据库

        Returns:
            存入的 chunk 数量
        """
        chunks = self.chunk_text(text)
        if not chunks:
            return 0

        # 批量嵌入（每次最多 20 条）
        all_embeddings = []
        batch_size = 20
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            embeddings = await self.embed_texts(batch)
            all_embeddings.extend(embeddings)

        # 存入数据库
        async with Database() as db:
            # 先清除旧 chunks
            await db.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))

            for idx, (chunk_text, embedding) in enumerate(zip(chunks, all_embeddings)):
                blob = self._embedding_to_blob(embedding)
                await db.execute(
                    "INSERT INTO chunks (item_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
                    (item_id, idx, chunk_text, blob)
                )
            await db.commit()

        return len(chunks)

    async def search_similar(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """向量检索：找最相似的 chunks

        注：简化版使用余弦相似度手动计算。
        生产环境应使用 sqlite-vec 扩展的 vec_distance 函数。
        """
        if not query.strip():
            return []

        # 嵌入查询
        query_emb = (await self.embed_texts([query]))[0]

        # 获取所有 chunks 的嵌入（简化版；大规模应用 sqlite-vec）
        async with Database() as db:
            cursor = await db.execute(
                "SELECT c.id, c.item_id, c.chunk_index, c.text, c.embedding, i.title "
                "FROM chunks c JOIN items i ON c.item_id = i.id "
                "WHERE c.embedding IS NOT NULL"
            )
            rows = await cursor.fetchall()

        if not rows:
            return []

        # 计算相似度
        results = []
        for row in rows:
            emb = self._blob_to_embedding(row[4])
            if emb:
                score = self._cosine_similarity(query_emb, emb)
                results.append({
                    'chunk_id': row[0],
                    'item_id': row[1],
                    'chunk_index': row[2],
                    'text': row[3],
                    'title': row[5],
                    'score': score,
                })

        # 排序取 top_k
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]

    @staticmethod
    def _embedding_to_blob(embedding: List[float]) -> bytes:
        """将嵌入向量转为 BLOB"""
        return struct.pack(f'{len(embedding)}f', *embedding)

    @staticmethod
    def _blob_to_embedding(blob: bytes) -> Optional[List[float]]:
        """从 BLOB 还原嵌入向量"""
        if not blob:
            return None
        n = len(blob) // 4
        return list(struct.unpack(f'{n}f', blob))

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """余弦相似度"""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
