"""M6 任务管线测试"""

import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


def test_stage_enum():
    """测试阶段枚举"""
    from src.pipeline import Stage, STAGE_NAMES
    assert Stage.PARSE == 1
    assert Stage.STORE == 7
    assert "解析链接" in STAGE_NAMES[Stage.PARSE]
    assert "入库" in STAGE_NAMES[Stage.STORE]


def test_task_context():
    """测试任务上下文"""
    from src.pipeline import TaskContext, Stage
    ctx = TaskContext(task_id=42, mode="semi", exit_stage=Stage.TRANSCRIBE)
    assert ctx.task_id == 42
    assert ctx.mode == "semi"
    assert ctx.exit_stage == 5
    assert ctx.status == "queued"


@pytest.mark.asyncio
async def test_pipeline_submit(tmp_path, monkeypatch):
    """测试任务提交"""
    import src.database as db_mod
    monkeypatch.setattr(db_mod, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db_mod, 'DB_PATH', tmp_path / "test.db")
    await db_mod.init_db()

    import src.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, 'WORKDIR', tmp_path / "workdir")

    from src.pipeline import Pipeline, Stage
    pipeline = Pipeline()

    task_id = await pipeline.submit(
        input_text="https://v.douyin.com/abc/",
        mode="auto",
    )
    assert task_id > 0

    # 半自动：本地音频入口
    task_id2 = await pipeline.submit(
        input_file="/tmp/test.mp3",
        mode="semi",
        exit_stage=Stage.TRANSCRIBE,
    )
    assert task_id2 > task_id


@pytest.mark.asyncio
async def test_pipeline_stage_execution(tmp_path, monkeypatch):
    """测试管线阶段执行（mock 所有外部依赖）"""
    import src.database as db_mod
    monkeypatch.setattr(db_mod, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db_mod, 'DB_PATH', tmp_path / "test.db")
    await db_mod.init_db()

    import src.pipeline as pl_mod
    import src.config as cfg_mod
    monkeypatch.setattr(pl_mod, 'WORKDIR', tmp_path / "workdir")

    # Mock 配置
    mock_config = MagicMock()
    mock_config.pipeline.split_size_mb = 45
    mock_config.pipeline.split_duration_min = 55
    mock_config.pipeline.segment_duration_sec = 540
    mock_config.asr.api_key = "test"
    mock_config.asr.base_url = "http://test/v1/audio/transcriptions"
    mock_config.asr.model = "test-model"
    mock_config.asr.concurrent = 2
    mock_config.llm.api_key = "test"
    mock_config.llm.base_url = "http://test/v1"
    mock_config.llm.model = "test-model"
    mock_config.llm.temperature = 0.3
    mock_config.embedding.api_key = "test"
    mock_config.embedding.base_url = "http://test/v1"
    mock_config.embedding.model = "test-model"
    mock_config.embedding.dimensions = 1024
    mock_config.embedding.chunk_size = 500
    mock_config.embedding.chunk_overlap = 50

    from src.pipeline import Pipeline, TaskContext, Stage

    pipeline = Pipeline()

    # 直接测试 _stage_summarize（使用 mock）
    ctx = TaskContext(
        task_id=99,
        entry_stage=Stage.SUMMARIZE,
        exit_stage=Stage.SUMMARIZE,
        work_dir=tmp_path / "workdir",
    )
    ctx.transcript = "这是一段测试转录文本"

    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock(message=MagicMock(content="## 总结\n测试内容"))]

    with patch('src.summary.AsyncOpenAI') as mock_openai:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_openai.return_value = mock_client
        monkeypatch.setattr(cfg_mod, '_config', mock_config)

        await pipeline._stage_summarize(ctx)

    assert "总结" in ctx.summary_md
    assert ctx.tags  # 应该有标签


def test_pipeline_has_stage_output(tmp_path):
    """测试断点检测"""
    from src.pipeline import Pipeline, TaskContext, Stage

    pipeline = Pipeline()
    ctx = TaskContext(task_id=1, work_dir=tmp_path)

    # 无产物
    assert pipeline._has_stage_output(ctx, Stage.TRANSCRIBE) is False

    # 有产物
    ctx.task_dir  # 创建目录
    (tmp_path / "1" / "transcript.txt").write_text("text")
    assert pipeline._has_stage_output(ctx, Stage.TRANSCRIBE) is True
