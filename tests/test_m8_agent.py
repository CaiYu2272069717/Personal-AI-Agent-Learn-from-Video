"""M8 AI Agent 框架测试"""

import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


def test_tool_registry():
    """测试工具注册表"""
    from src.agent.tools import ToolRegistry
    registry = ToolRegistry()

    # 内置工具数量
    tools = registry.list_tools()
    assert len(tools) >= 11  # 至少 11 个

    # 验证每个工具有必要字段
    for t in tools:
        assert t["type"] == "function"
        assert "name" in t["function"]
        assert "description" in t["function"]
        assert "parameters" in t["function"]

    # 获取特定工具
    tool = registry.get_tool("search_knowledge")
    assert tool is not None
    assert tool.risk_level == "safe"

    tool2 = registry.get_tool("run_command")
    assert tool2 is not None
    assert tool2.risk_level == "high"


def test_permission_safe_tools():
    """测试安全工具权限"""
    from src.agent.permissions import PermissionManager
    pm = PermissionManager()

    allowed, reason = pm.check_tool_permission("search_knowledge", "safe", {"query": "test"})
    assert allowed is True
    assert reason is None


def test_permission_medium_tool_default_mode():
    """测试中危工具在默认模式下需确认"""
    from src.agent.permissions import PermissionManager
    pm = PermissionManager()

    allowed, reason = pm.check_tool_permission(
        "write_file", "medium",
        {"path": "/tmp/test.txt", "content": "hello"}
    )
    assert allowed is False
    assert "写入文件确认" in reason


def test_permission_high_tool_default_mode():
    """测试高危工具在默认模式下需确认"""
    from src.agent.permissions import PermissionManager
    pm = PermissionManager()

    allowed, reason = pm.check_tool_permission(
        "run_command", "high",
        {"command": "ls -la"}
    )
    assert allowed is False
    assert "执行命令确认" in reason


def test_permission_blacklist():
    """测试命令黑名单（永远拦截）"""
    from src.agent.permissions import PermissionManager
    pm = PermissionManager()

    allowed, reason = pm.check_tool_permission(
        "run_command", "high",
        {"command": "rm -rf /"}
    )
    assert allowed is False
    assert "永久拦截" in reason


def test_permission_full_access(monkeypatch):
    """测试完全访问模式"""
    from src.agent.permissions import PermissionManager
    from src.config import get_config

    config = get_config()
    original = config.agent_permission.full_access
    config.agent_permission.full_access = True

    try:
        pm = PermissionManager()
        # 中危直接放行
        allowed, _ = pm.check_tool_permission(
            "write_file", "medium", {"path": "x", "content": "y"}
        )
        assert allowed is True

        # 黑名单仍拦截
        allowed2, reason2 = pm.check_tool_permission(
            "run_command", "high", {"command": "rm -rf /"}
        )
        assert allowed2 is False
    finally:
        config.agent_permission.full_access = original


def test_file_boundary_within_project():
    """测试文件边界：项目内"""
    from src.agent.permissions import PermissionManager
    from src.config import BASE_DIR

    pm = PermissionManager()
    path = str(BASE_DIR / "test.txt")
    within, reason = pm.check_file_boundary(path)
    assert within is True


def test_file_boundary_outside_project():
    """测试文件边界：项目外需确认"""
    from src.agent.permissions import PermissionManager
    pm = PermissionManager()

    within, reason = pm.check_file_boundary("C:/Users/other/secret.txt")
    assert within is False
    assert "越界" in reason


def test_agent_memory():
    """测试 Agent 记忆管理"""
    from src.agent.memory import load_memory, get_dynamic_section

    content = load_memory()
    assert "固定区" in content
    assert "可变区" in content

    dynamic = get_dynamic_section()
    assert isinstance(dynamic, str)


@pytest.mark.asyncio
async def test_tool_read_file(tmp_path):
    """测试 read_file 工具"""
    from src.agent.tools import ToolRegistry
    registry = ToolRegistry()

    test_file = tmp_path / "hello.txt"
    test_file.write_text("Hello World", encoding='utf-8')

    result = await registry.execute("read_file", {"path": str(test_file)})
    assert "Hello World" in result


@pytest.mark.asyncio
async def test_tool_write_file(tmp_path):
    """测试 write_file 工具"""
    from src.agent.tools import ToolRegistry
    registry = ToolRegistry()

    target = tmp_path / "output.txt"
    result = await registry.execute("write_file", {
        "path": str(target),
        "content": "test content"
    })
    data = json.loads(result)
    assert data["status"] == "ok"
    assert target.read_text() == "test content"


@pytest.mark.asyncio
async def test_tool_list_dir(tmp_path):
    """测试 list_dir 工具"""
    from src.agent.tools import ToolRegistry
    registry = ToolRegistry()

    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b").mkdir()

    result = await registry.execute("list_dir", {"path": str(tmp_path)})
    items = json.loads(result)
    names = [i["name"] for i in items]
    assert "a.txt" in names
    assert "b" in names


@pytest.mark.asyncio
async def test_tool_edit_file(tmp_path):
    """测试 edit_file 工具"""
    from src.agent.tools import ToolRegistry
    registry = ToolRegistry()

    target = tmp_path / "edit_me.txt"
    target.write_text("Hello World", encoding='utf-8')

    result = await registry.execute("edit_file", {
        "path": str(target),
        "old_text": "World",
        "new_text": "Python",
    })
    data = json.loads(result)
    assert data["status"] == "ok"
    assert target.read_text() == "Hello Python"


@pytest.mark.asyncio
async def test_tool_unknown():
    """测试调用未知工具"""
    from src.agent.tools import ToolRegistry
    registry = ToolRegistry()

    result = await registry.execute("nonexistent_tool", {})
    data = json.loads(result)
    assert "error" in data
