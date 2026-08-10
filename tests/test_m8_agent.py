"""M8 AI Agent 框架测试"""

import pytest
import asyncio
import json
import subprocess
import sys
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


def test_explicit_tool_routing():
    """明确的用户动作应稳定路由到对应工具，普通问答保持模型自主选择。"""
    from src.agent.tools import infer_explicit_tool_name

    cases = {
        "在知识库中搜索 Agent，并注明条目标题": "search_knowledge",
        "读取知识库条目 1 后总结": "get_item",
        "搜索 Python 官方网站并给出来源 URL": "web_search",
        "抓取 https://example.com 并概括": "web_fetch",
        "列出 workspace 目录": "list_dir",
        "读取 workspace/README.md": "read_file",
        "找出 workspace 下所有 markdown 文件": "glob_files",
        "用受限 Python 沙箱计算 1 到 100 的和": "run_python_sandbox",
        "把 hello 写入 workspace/eval.txt": "write_file",
        "执行命令 python --version": "run_command",
        "识别 workspace/sample.png 中的文字": "ocr_image",
    }
    for prompt, expected in cases.items():
        assert infer_explicit_tool_name(prompt) == expected

    assert infer_explicit_tool_name("用一句话解释什么是 RAG。") is None


def test_explicit_tool_call_argument_parsing():
    """参数完整的显式请求应解析为可直接执行的工具调用。"""
    from src.agent.tools import infer_explicit_tool_call

    assert infer_explicit_tool_call("搜索 Python 官方网站并给出来源 URL。") == (
        "web_search", {"query": "Python 官方网站"}
    )
    assert infer_explicit_tool_call("列出 workspace 目录。") == (
        "list_dir", {"path": "workspace"}
    )
    assert infer_explicit_tool_call("读取 workspace/README.md。") == (
        "read_file", {"path": "workspace/README.md"}
    )
    assert infer_explicit_tool_call("执行命令 python --version。") == (
        "run_command", {"command": "python --version"}
    )
    assert infer_explicit_tool_call("读取知识库条目 1 后总结。") == (
        "get_item", {"item_id": 1}
    )


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


@pytest.mark.asyncio
async def test_python_sandbox_executes_calculation_and_blocks_imports():
    """代码沙箱可用于计算，但不能导入系统模块。"""
    from src.agent.tools import ToolRegistry

    registry = ToolRegistry()
    result = json.loads(await registry.execute("run_python_sandbox", {
        "code": "values = [1, 2, 3, 4]\nprint(sum(values), math.sqrt(81))",
    }))
    assert result["returncode"] == 0
    assert "10 9.0" in result["stdout"]
    assert result["sandbox"] == "restricted-python"

    blocked = json.loads(await registry.execute("run_python_sandbox", {
        "code": "import os\nprint(os.getcwd())",
    }))
    assert blocked["returncode"] == 1
    assert "不允许的语法" in blocked["stderr"]


@pytest.mark.asyncio
async def test_agent_confirmation_is_actually_resolved():
    """确认接口使用 Future 真正阻塞/放行，而不是显示后自动执行。"""
    from src.agent.core import AgentCore

    agent = AgentCore()
    future = asyncio.get_running_loop().create_future()
    agent._pending_confirmations["call-1"] = future
    assert agent.resolve_confirmation("call-1", False) is True
    assert await future is False
    assert agent.resolve_confirmation("missing", True) is False


@pytest.mark.asyncio
async def test_agent_auto_approve_pending_when_full_access_enabled():
    """开启完全访问后，待确认的工具请求应被自动批准。"""
    from src.agent.core import AgentCore

    agent = AgentCore()
    f1 = asyncio.get_running_loop().create_future()
    f2 = asyncio.get_running_loop().create_future()
    agent._pending_confirmations["call-1"] = f1
    agent._pending_confirmations["call-2"] = f2

    agent.auto_approve_all_pending()

    assert await f1 is True
    assert await f2 is True


@pytest.mark.asyncio
async def test_agent_run_continues_without_a_stream_consumer(tmp_path, monkeypatch):
    """后台 run 不依赖 SSE 消费者也会执行完成并持久化事件。"""
    from src import database
    from src.agent.run_manager import AgentRunManager

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent-runs.db")
    await database.init_db()

    class FakeAgent:
        async def chat(self, message, conversation_id, history, user_message_id=0):
            yield {"type": "status", "status": "thinking", "label": "正在思考"}
            await asyncio.sleep(0.03)
            yield {"type": "text", "content": "后台完成"}
            yield {"type": "done", "content": "后台完成", "conversation_id": conversation_id}

    async with database.Database() as db:
        cursor = await db.execute("INSERT INTO conversations (title) VALUES ('test')")
        await db.commit()
        conversation_id = cursor.lastrowid

    manager = AgentRunManager(FakeAgent())
    run_id = await manager.start("hello", conversation_id, [])
    # 刻意不连接任何 SSE stream。
    await asyncio.sleep(0.12)

    async with database.Database() as db:
        cursor = await db.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT event_json FROM agent_run_events WHERE run_id = ? ORDER BY id", (run_id,)
        )
        events = [json.loads(row[0]) for row in await cursor.fetchall()]

    assert run[0] == "completed"
    assert [event["type"] for event in events] == ["status", "text", "done"]


@pytest.mark.asyncio
async def test_revert_restores_multiple_turns_and_removes_new_files(tmp_path, monkeypatch):
    """回退一轮会倒序恢复后续文件快照，并裁剪对应会话消息。"""
    from src import database
    from src.agent.tools import ToolRegistry
    from src.routes.agent_router import RevertRequest, revert

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "revert.db")
    await database.init_db()

    target = tmp_path / "tracked.txt"
    created = tmp_path / "created.txt"
    target.write_text("original", encoding="utf-8")

    async with database.Database() as db:
        cursor = await db.execute("INSERT INTO conversations (title) VALUES ('turn one')")
        conversation_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', 'one')",
            (conversation_id,),
        )
        turn_one = cursor.lastrowid
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', 'done one')",
            (conversation_id,),
        )
        cursor = await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', 'two')",
            (conversation_id,),
        )
        turn_two = cursor.lastrowid
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', 'done two')",
            (conversation_id,),
        )
        await db.commit()

    registry = ToolRegistry()
    await registry.execute(
        "write_file", {"path": str(target), "content": "first"},
        conversation_id, turn_one,
    )
    await registry.execute(
        "write_file", {"path": str(target), "content": "second"},
        conversation_id, turn_two,
    )
    await registry.execute(
        "write_file", {"path": str(created), "content": "new"},
        conversation_id, turn_two,
    )

    result = await revert(RevertRequest(
        conversation_id=conversation_id, user_message_id=turn_one,
    ))

    assert result["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "original"
    assert not created.exists()
    async with database.Database() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
        )
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.execute(
            "SELECT COUNT(*) FROM snapshots WHERE conversation_id = ?", (conversation_id,)
        )
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_run_command_changes_are_captured_for_revert(tmp_path, monkeypatch):
    """shell 命令造成的未知项目文件改动也会进入当前轮检查点。"""
    from src import database
    from src.agent.snapshots import restore_from_turn
    from src.agent.tools import ToolRegistry
    from src.config import WORKSPACE_DIR

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "command-revert.db")
    await database.init_db()
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    target = WORKSPACE_DIR / f"revert-command-{tmp_path.name}.txt"
    if target.exists():
        target.unlink()

    async with database.Database() as db:
        cursor = await db.execute("INSERT INTO conversations (title) VALUES ('command')")
        conversation_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', 'run')",
            (conversation_id,),
        )
        user_message_id = cursor.lastrowid
        await db.commit()

    code = f"from pathlib import Path; Path({str(target)!r}).write_text('created', encoding='utf-8')"
    command = subprocess.list2cmdline([sys.executable, "-c", code])
    result = json.loads(await ToolRegistry().execute(
        "run_command", {"command": command}, conversation_id, user_message_id,
    ))
    assert result["returncode"] == 0
    assert target.read_text(encoding="utf-8") == "created"

    restored = await restore_from_turn(conversation_id, user_message_id)
    assert restored["removed_files"] >= 1
    assert not target.exists()
