"""Agent 全局记忆管理：固定区 + 可变区"""

from pathlib import Path
from ..config import WORKSPACE_DIR

MEMORY_FILE = WORKSPACE_DIR / "agent_memory.md"

FIXED_START = "<!-- FIXED:START -->"
FIXED_END = "<!-- FIXED:END -->"
DYNAMIC_START = "<!-- DYNAMIC:START -->"
DYNAMIC_END = "<!-- DYNAMIC:END -->"


def load_memory() -> str:
    """加载完整记忆内容"""
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text(encoding='utf-8')
    return ""


def get_dynamic_section() -> str:
    """获取可变区内容"""
    content = load_memory()
    start_idx = content.find(DYNAMIC_START)
    end_idx = content.find(DYNAMIC_END)
    if start_idx == -1 or end_idx == -1:
        return ""
    return content[start_idx + len(DYNAMIC_START):end_idx].strip()


def update_dynamic_section(new_content: str) -> None:
    """更新可变区内容（模型可调用）"""
    content = load_memory()
    start_idx = content.find(DYNAMIC_START)
    end_idx = content.find(DYNAMIC_END)

    if start_idx == -1 or end_idx == -1:
        return

    before = content[:start_idx + len(DYNAMIC_START)]
    after = content[end_idx:]

    new_full = f"{before}\n{new_content}\n{after}"
    MEMORY_FILE.write_text(new_full, encoding='utf-8')


def clear_dynamic_section() -> None:
    """清空可变区"""
    update_dynamic_section(
        "（暂空——工作中学到的本机重要目录、常用命令、用户偏好、项目背景等将记录于此。）"
    )
