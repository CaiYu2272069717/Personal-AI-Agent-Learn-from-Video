"""M5 知识库模块测试"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_knowledge_db_crud(tmp_path, monkeypatch):
    """测试知识库 CRUD"""
    import src.database as db_mod
    monkeypatch.setattr(db_mod, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db_mod, 'DB_PATH', tmp_path / "test.db")
    await db_mod.init_db()

    from src.knowledge.db import KnowledgeDB
    kb = KnowledgeDB()

    # Create
    item_id = await kb.create_item(
        source="douyin",
        source_id="test_001",
        title="测试条目",
        url="https://douyin.com/test",
        author="作者A",
        duration_sec=120,
        transcript="这是转录文本",
        summary_md="## 总结\n这是总结",
        tags="测试,知识",
    )
    assert item_id > 0

    # Read
    item = await kb.get_item(item_id)
    assert item is not None
    assert item['title'] == "测试条目"
    assert item['source'] == "douyin"
    assert item['transcript'] == "这是转录文本"

    # Read by source_id
    item2 = await kb.get_item_by_source_id("test_001")
    assert item2['id'] == item_id

    # Update
    await kb.update_item(item_id, title="更新标题", tags="新标签")
    item3 = await kb.get_item(item_id)
    assert item3['title'] == "更新标题"
    assert item3['tags'] == "新标签"

    # List
    items, total = await kb.list_items()
    assert total == 1
    assert len(items) == 1

    # Delete
    await kb.delete_item(item_id)
    item4 = await kb.get_item(item_id)
    assert item4 is None


@pytest.mark.asyncio
async def test_knowledge_fts_search(tmp_path, monkeypatch):
    """测试 FTS5 全文检索"""
    import src.database as db_mod
    monkeypatch.setattr(db_mod, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db_mod, 'DB_PATH', tmp_path / "test.db")
    await db_mod.init_db()

    from src.knowledge.db import KnowledgeDB
    kb = KnowledgeDB()

    await kb.create_item(
        source="bilibili", source_id="bv001",
        title="Python装饰器教程",
        transcript="装饰器是Python中非常重要的特性",
        summary_md="讲解了Python装饰器的用法",
        tags="Python,编程",
    )
    await kb.create_item(
        source="article", source_id="art001",
        title="JavaScript异步编程",
        transcript="Promise和async await的使用",
        summary_md="JS异步编程指南",
        tags="JavaScript,前端",
    )

    # 搜索 Python
    results = await kb.search_fts("Python 装饰器")
    assert len(results) >= 1
    assert results[0]['title'] == "Python装饰器教程"

    # 搜索 JavaScript
    results2 = await kb.search_fts("异步")
    assert len(results2) >= 1


def test_markdown_generation():
    """测试 Markdown 内容生成"""
    from src.knowledge.markdown import generate_md_content

    content = generate_md_content(
        title="测试标题",
        summary_md="## 总结\n核心内容",
        transcript="原始转录文本",
        source="douyin",
        url="https://douyin.com/test",
        author="作者",
        tags="标签1,标签2",
        duration_sec=125,
    )

    assert "# 测试标题" in content
    assert "| 来源 | douyin |" in content
    assert "| 作者 | 作者 |" in content
    assert "| 时长 | 2分5秒 |" in content
    assert "## 总结" in content
    assert "<details>" in content
    assert "原始转录文本" in content


def test_save_markdown(tmp_path, monkeypatch):
    """测试 Markdown 文件保存"""
    import src.knowledge.markdown as md_mod
    monkeypatch.setattr(md_mod, 'LIBRARY_DIR', tmp_path)

    from src.knowledge.markdown import save_markdown

    path = save_markdown(
        title="测试保存",
        summary_md="内容",
        source="test",
    )

    assert path.exists()
    assert path.suffix == ".md"
    assert "测试保存" in path.stem
    content = path.read_text(encoding='utf-8')
    assert "# 测试保存" in content


def test_save_markdown_no_overwrite(tmp_path, monkeypatch):
    """测试不覆盖已有文件"""
    import src.knowledge.markdown as md_mod
    monkeypatch.setattr(md_mod, 'LIBRARY_DIR', tmp_path)

    from src.knowledge.markdown import save_markdown

    path1 = save_markdown(title="same", summary_md="v1", custom_filename="test.md")
    path2 = save_markdown(title="same", summary_md="v2", custom_filename="test.md")

    assert path1 != path2
    assert path1.exists()
    assert path2.exists()


def test_embedding_chunk_text():
    """测试文本分块"""
    from src.knowledge.embeddings import EmbeddingClient

    with patch('src.knowledge.embeddings.get_config') as mock_cfg:
        mock_cfg.return_value = MagicMock(
            embedding=MagicMock(
                base_url="http://test",
                api_key="key",
                model="model",
                dimensions=1024,
                chunk_size=100,
                chunk_overlap=10,
            )
        )
        client = EmbeddingClient(api_key="test")

    # 短文本不分
    chunks = client.chunk_text("短文本")
    assert len(chunks) == 1

    # 长文本分块
    long_text = "这是一句话。" * 200
    chunks = client.chunk_text(long_text)
    assert len(chunks) > 1
    # 每块不应超过限制
    max_chars = int(client.chunk_size * 1.5)
    for c in chunks[:-1]:  # 最后一块可能较短
        assert len(c) <= max_chars + 50  # 留余量


def test_embedding_blob_conversion():
    """测试嵌入向量 BLOB 转换"""
    from src.knowledge.embeddings import EmbeddingClient

    vec = [0.1, 0.2, 0.3, -0.5, 1.0]
    blob = EmbeddingClient._embedding_to_blob(vec)
    assert isinstance(blob, bytes)

    restored = EmbeddingClient._blob_to_embedding(blob)
    assert len(restored) == 5
    for a, b in zip(vec, restored):
        assert abs(a - b) < 1e-6


def test_cosine_similarity():
    """测试余弦相似度"""
    from src.knowledge.embeddings import EmbeddingClient

    # 相同向量 = 1.0
    assert abs(EmbeddingClient._cosine_similarity([1, 0], [1, 0]) - 1.0) < 1e-6
    # 正交向量 = 0.0
    assert abs(EmbeddingClient._cosine_similarity([1, 0], [0, 1]) - 0.0) < 1e-6
    # 反方向 = -1.0
    assert abs(EmbeddingClient._cosine_similarity([1, 0], [-1, 0]) - (-1.0)) < 1e-6
