"""M4 LLM 总结模块测试"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock


def test_split_text():
    """测试文本分割"""
    from src.summary import SummaryClient

    # 短文本不分割
    chunks = SummaryClient._split_text("短文本", 100)
    assert len(chunks) == 1

    # 长文本按句号分割
    text = "第一句话。" * 50 + "第二部分内容。" * 50
    chunks = SummaryClient._split_text(text, 200)
    assert len(chunks) > 1
    # 合并后应等于原文
    assert ''.join(chunks) == text


def test_builtin_templates():
    """测试内置模板加载"""
    from src.summary import SummaryClient

    client_mock = MagicMock()
    with patch('src.summary.AsyncOpenAI', return_value=client_mock):
        sc = SummaryClient(api_key="test")

    template = sc._builtin_template("knowledge")
    assert "一句话总结" in template
    assert "核心要点" in template
    assert "行动清单" in template

    template2 = sc._builtin_template("tutorial")
    assert "步骤详解" in template2

    template3 = sc._builtin_template("quick")
    assert "3-5 句话" in template3


@pytest.mark.asyncio
async def test_summarize_short_text():
    """测试短文本单次总结"""
    from src.summary import SummaryClient

    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock(message=MagicMock(content="## 一句话总结\n这是总结"))]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with patch('src.summary.AsyncOpenAI', return_value=mock_client):
        sc = SummaryClient(api_key="test")
        result = await sc.summarize("短文本内容", title="测试标题")

    assert "一句话总结" in result
    mock_client.chat.completions.create.assert_called_once()


@pytest.mark.asyncio
async def test_summarize_long_text_map_reduce():
    """测试长文本 map-reduce 总结"""
    from src.summary import SummaryClient, MAX_CHUNK_CHARS

    # 创建超长文本
    long_text = "这是一段很长的内容。" * (MAX_CHUNK_CHARS // 5)

    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock(message=MagicMock(content="要点摘要"))]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with patch('src.summary.AsyncOpenAI', return_value=mock_client):
        sc = SummaryClient(api_key="test")
        result = await sc.summarize(long_text, title="长文")

    # map + reduce 至少调用 2 次以上
    assert mock_client.chat.completions.create.call_count >= 2


@pytest.mark.asyncio
async def test_generate_tags():
    """测试自动标签生成"""
    from src.summary import SummaryClient

    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock(message=MagicMock(content="Python, 装饰器, 编程技巧"))]

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with patch('src.summary.AsyncOpenAI', return_value=mock_client):
        sc = SummaryClient(api_key="test")
        tags = await sc.generate_tags("关于 Python 装饰器的内容")

    assert "Python" in tags
    assert "装饰器" in tags
    assert len(tags) == 3
