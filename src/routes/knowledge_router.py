"""知识库 API 路由"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from ..knowledge.db import KnowledgeDB

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/items")
async def list_items(page: int = 1, size: int = 20, q: str = "", source: str = ""):
    """知识库条目列表（支持搜索）"""
    kb = KnowledgeDB()

    if q:
        items = await kb.search_fts(q, limit=size)
        return {"items": items, "total": len(items), "page": 1}

    items, total = await kb.list_items(page=page, size=size, source=source or None)
    return {"items": items, "total": total, "page": page}


@router.get("/items/{item_id}")
async def get_item(item_id: int):
    """获取条目详情"""
    kb = KnowledgeDB()
    item = await kb.get_item(item_id)
    if not item:
        return {"error": "not_found"}
    return item


@router.delete("/items/{item_id}")
async def delete_item(item_id: int):
    """删除条目"""
    kb = KnowledgeDB()
    await kb.delete_item(item_id)
    return {"status": "ok"}


@router.post("/items/{item_id}/resummarize")
async def resummarize(item_id: int, template: str = "knowledge"):
    """重新总结（不重复转录）"""
    kb = KnowledgeDB()
    item = await kb.get_item(item_id)
    if not item:
        return {"error": "not_found"}

    if not item.get('transcript'):
        return {"error": "无转录文本，无法重新总结"}

    from ..summary import SummaryClient
    client = SummaryClient()
    new_summary = await client.summarize(
        item['transcript'], title=item.get('title', ''), template=template
    )
    tags = await client.generate_tags(new_summary)

    await kb.update_item(
        item_id,
        summary_md=new_summary,
        tags=','.join(tags),
    )

    return {"status": "ok", "summary": new_summary[:200]}


@router.post("/rebuild-index")
async def rebuild_index():
    """一键补建嵌入索引"""
    kb = KnowledgeDB()
    items = await kb.get_all_items_for_embedding()

    if not items:
        return {"status": "ok", "count": 0, "message": "无需要嵌入的条目"}

    from ..knowledge.embeddings import EmbeddingClient
    emb = EmbeddingClient()
    count = 0

    for item in items:
        text = f"{item['title']}\n{item.get('summary_md', '')}\n{item.get('transcript', '')[:3000]}"
        chunks = await emb.embed_and_store(item['id'], text)
        count += chunks

    return {"status": "ok", "items_processed": len(items), "chunks_created": count}
