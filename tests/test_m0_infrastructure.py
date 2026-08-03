"""M0 基础设施测试：配置、数据库、应用启动"""

import json
import asyncio
import pytest
from pathlib import Path
from dataclasses import asdict


def test_config_defaults():
    """测试默认配置加载"""
    from src.config import AppConfig, ASRConfig, LLMConfig
    config = AppConfig()
    assert config.asr.model == "FunAudioLLM/SenseVoiceSmall"
    assert config.llm.temperature == 0.3
    assert config.agent_llm.supports_function_calling is True
    assert config.embedding.dimensions == 1024
    assert config.pipeline.segment_duration_sec == 540
    assert config.host == "127.0.0.1"
    assert config.port == 8000


def test_config_load_from_file(tmp_path):
    """测试从文件加载配置"""
    import src.config as cfg
    # 临时修改 CONFIG_FILE
    original_path = cfg.CONFIG_FILE
    test_file = tmp_path / "config.local.json"
    test_file.write_text(json.dumps({
        "asr": {"api_key": "test-key-123", "model": "custom-model"},
        "port": 9999
    }))
    cfg.CONFIG_FILE = test_file
    try:
        config = cfg.load_config()
        assert config.asr.api_key == "test-key-123"
        assert config.asr.model == "custom-model"
        assert config.port == 9999
        # 未修改的保持默认
        assert config.llm.model == "deepseek-ai/DeepSeek-V3"
    finally:
        cfg.CONFIG_FILE = original_path


def test_config_save(tmp_path):
    """测试配置保存"""
    import src.config as cfg
    original_path = cfg.CONFIG_FILE
    test_file = tmp_path / "config.local.json"
    cfg.CONFIG_FILE = test_file
    try:
        config = cfg.AppConfig()
        config.asr.api_key = "save-test-key"
        cfg.save_config(config)
        assert test_file.exists()
        data = json.loads(test_file.read_text())
        assert data["asr"]["api_key"] == "save-test-key"
    finally:
        cfg.CONFIG_FILE = original_path


def test_config_deep_update():
    """测试深度合并"""
    from src.config import _deep_update
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    override = {"a": {"x": 10}, "c": 4}
    result = _deep_update(base, override)
    assert result["a"]["x"] == 10
    assert result["a"]["y"] == 2
    assert result["b"] == 3
    assert result["c"] == 4


@pytest.mark.asyncio
async def test_database_init(tmp_path, monkeypatch):
    """测试数据库初始化"""
    import src.database as db_mod
    monkeypatch.setattr(db_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")

    await db_mod.init_db()
    assert (tmp_path / "test.db").exists()

    # 验证表存在
    import aiosqlite
    async with aiosqlite.connect(str(tmp_path / "test.db")) as conn:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in await cursor.fetchall()]
        assert "items" in tables
        assert "chunks" in tables
        assert "conversations" in tables
        assert "messages" in tables
        assert "tasks" in tables
        assert "snapshots" in tables
        assert "items_fts" in tables


@pytest.mark.asyncio
async def test_database_context_manager(tmp_path, monkeypatch):
    """测试数据库上下文管理器"""
    import src.database as db_mod
    monkeypatch.setattr(db_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")

    await db_mod.init_db()

    async with db_mod.Database() as db:
        await db.execute(
            "INSERT INTO items (source, source_id, title) VALUES (?, ?, ?)",
            ("test", "test-001", "Test Item")
        )
        await db.commit()
        cursor = await db.execute("SELECT title FROM items WHERE source_id = ?", ("test-001",))
        row = await cursor.fetchone()
        assert row[0] == "Test Item"


def test_app_creation():
    """测试 FastAPI 应用创建"""
    from src.app import create_app
    app = create_app()
    assert app.title == "Learn From Video"
    # 检查路由注册（通过 openapi schema）
    openapi = app.openapi()
    paths = list(openapi.get("paths", {}).keys())
    assert "/" in paths or any("pipeline" in p for p in paths)
    assert any("knowledge" in p for p in paths)
    assert any("agent" in p for p in paths)
    assert any("settings" in p for p in paths)
