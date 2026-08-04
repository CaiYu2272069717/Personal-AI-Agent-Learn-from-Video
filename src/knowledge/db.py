"""知识库数据库操作：CRUD + FTS5 检索"""

import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime

import aiosqlite

from ..database import get_db, Database


class KnowledgeDB:
    """知识库 CRUD 操作"""

    async def create_item(
        self,
        source: str,
        source_id: str,
        title: str,
        url: str = "",
        author: str = "",
        duration_sec: int = 0,
        transcript: str = "",
        summary_md: str = "",
        tags: str = "",
        md_path: str = "",
        status: str = "completed",
    ) -> int:
        """创建知识条目，返回 ID"""
        async with Database() as db:
            cursor = await db.execute(
                """INSERT INTO items
                   (source, source_id, title, url, author, duration_sec,
                    transcript, summary_md, tags, md_path, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, source_id, title, url, author, duration_sec,
                 transcript, summary_md, tags, md_path, status),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        """获取单条知识条目"""
        async with Database() as db:
            cursor = await db.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def get_item_by_source_id(self, source_id: str) -> Optional[Dict[str, Any]]:
        """根据 source_id 查找（去重用）"""
        async with Database() as db:
            cursor = await db.execute(
                "SELECT * FROM items WHERE source_id = ?", (source_id,)
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def list_items(
        self,
        page: int = 1,
        size: int = 20,
        source: Optional[str] = None,
    ) -> tuple:
        """列表查询（时间倒序），返回 (items, total)"""
        offset = (page - 1) * size
        conditions = []
        params = []

        if source:
            conditions.append("source = ?")
            params.append(source)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with Database() as db:
            # 总数
            cursor = await db.execute(f"SELECT COUNT(*) FROM items {where}", params)
            total = (await cursor.fetchone())[0]

            # 分页
            cursor = await db.execute(
                f"SELECT * FROM items {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [size, offset]
            )
            rows = await cursor.fetchall()
            items = [dict(r) for r in rows]

        return items, total

    async def search_fts(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """FTS5 全文检索"""
        # FTS5 查询：用 * 做前缀匹配，提升中文召回
        # 将空格分隔的词用 OR 连接
        terms = query.strip().split()
        if terms:
            fts_query = ' OR '.join(f'"{t}"' for t in terms)
        else:
            fts_query = query

        try:
            async with Database() as db:
                cursor = await db.execute(
                    """SELECT items.* FROM items_fts
                       JOIN items ON items.id = items_fts.rowid
                       WHERE items_fts MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (fts_query, limit)
                )
                rows = await cursor.fetchall()
                results = [dict(r) for r in rows]
                if results:
                    return results
        except Exception:
            pass

        # FTS 无结果或异常时回退到 LIKE
        async with Database() as db:
            like_q = f"%{query}%"
            cursor = await db.execute(
                """SELECT * FROM items
                   WHERE title LIKE ? OR transcript LIKE ? OR summary_md LIKE ? OR tags LIKE ?
                   LIMIT ?""",
                (like_q, like_q, like_q, like_q, limit)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_item(self, item_id: int, **kwargs) -> None:
        """更新条目字段"""
        if not kwargs:
            return
        kwargs['updated_at'] = datetime.now().isoformat()
        set_clause = ', '.join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [item_id]

        async with Database() as db:
            await db.execute(
                f"UPDATE items SET {set_clause} WHERE id = ?",
                values
            )
            await db.commit()

    async def delete_item(self, item_id: int) -> None:
        """删除条目（级联删除 chunks）"""
        async with Database() as db:
            await db.execute("DELETE FROM chunks WHERE item_id = ?", (item_id,))
            await db.execute("DELETE FROM items WHERE id = ?", (item_id,))
            await db.commit()

    async def get_all_items_for_embedding(self) -> List[Dict[str, Any]]:
        """获取所有需要嵌入的条目（有 summary 但无 chunks）"""
        async with Database() as db:
            cursor = await db.execute(
                """SELECT i.* FROM items i
                   WHERE i.summary_md != ''
                   AND i.id NOT IN (SELECT DISTINCT item_id FROM chunks)"""
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
