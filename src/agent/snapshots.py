"""Agent 文件检查点。

每个快照保存某轮首次改动文件之前的内容。回退多轮时按倒序应用，
因此既能恢复被覆盖/删除的文件，也能移除由 Agent 新建的文件。
"""

import asyncio
import os
from pathlib import Path
from typing import Dict, Optional

from ..config import BASE_DIR
from ..database import Database


# 应用数据库、缓存和大体积运行产物不参与 shell 命令的全项目扫描。
# write_file/edit_file 直接修改其中单个文件时，仍会精确记录该文件。
_SCAN_EXCLUDED_DIRS = {
    ".git", ".venv", "venv", ".pytest_cache", "__pycache__",
    "data", "output", "temp", ".playwright-cli", ".workbuddy", ".test-tmp",
    "conversations", "workdir", "library",
}
_UNSET = object()


def _normalise_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    return candidate.resolve(strict=False)


def _read_file(path: Path) -> Optional[bytes]:
    if path.exists() and path.is_file():
        return path.read_bytes()
    return None


def _capture_project_files() -> Dict[str, bytes]:
    """读取可回退的项目文件；用于捕获 shell 命令造成的未知改动。"""
    state: Dict[str, bytes] = {}
    for root, dirnames, filenames in os.walk(BASE_DIR):
        dirnames[:] = [name for name in dirnames if name not in _SCAN_EXCLUDED_DIRS]
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            if path.is_symlink():
                continue
            try:
                state[str(path.resolve(strict=False))] = path.read_bytes()
            except (OSError, PermissionError):
                continue
    return state


async def record_file_before(
    conversation_id: int,
    user_message_id: int,
    path: str | Path,
    content_before: Optional[bytes] | object = _UNSET,
) -> None:
    """仅记录该轮对一个文件的第一次改动前状态。"""
    if not conversation_id or not user_message_id:
        return
    resolved = _normalise_path(path)
    if content_before is _UNSET:
        content_before = await asyncio.to_thread(_read_file, resolved)
    async with Database() as db:
        await db.execute(
            """INSERT INTO snapshots
                   (conversation_id, turn_index, file_path, content_before)
               SELECT ?, ?, ?, ?
               WHERE NOT EXISTS (
                   SELECT 1 FROM snapshots
                   WHERE conversation_id = ? AND turn_index = ? AND file_path = ?
               )""",
            (
                conversation_id, user_message_id, str(resolved), content_before,
                conversation_id, user_message_id, str(resolved),
            ),
        )
        await db.commit()


async def capture_project_state() -> Dict[str, bytes]:
    return await asyncio.to_thread(_capture_project_files)


async def record_project_changes(
    conversation_id: int,
    user_message_id: int,
    before: Dict[str, bytes],
) -> int:
    """比较命令执行前后状态并保存所有发生变化的项目文件。"""
    if not conversation_id or not user_message_id:
        return 0
    after = await capture_project_state()
    changed = [path for path in before.keys() | after.keys() if before.get(path) != after.get(path)]
    for path in changed:
        await record_file_before(conversation_id, user_message_id, path, before.get(path))
    return len(changed)


async def restore_from_turn(conversation_id: int, user_message_id: int) -> dict:
    """将指定轮次及之后的文件改动倒序还原。"""
    async with Database() as db:
        cursor = await db.execute(
            """SELECT file_path, content_before FROM snapshots
               WHERE conversation_id = ? AND turn_index >= ?
               ORDER BY turn_index DESC, id DESC""",
            (conversation_id, user_message_id),
        )
        rows = await cursor.fetchall()

    restored_paths: set[Path] = set()
    removed_paths: set[Path] = set()
    touched_parents: set[Path] = set()
    for row in rows:
        path = _normalise_path(row[0])
        content = row[1]
        touched_parents.add(path.parent)
        if content is None:
            if path.exists() and (path.is_file() or path.is_symlink()):
                path.unlink()
            removed_paths.add(path)
            restored_paths.discard(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(content))
            restored_paths.add(path)
            removed_paths.discard(path)

    # 清理由回退留下的空目录，且绝不越过项目根目录。
    base = BASE_DIR.resolve(strict=False)
    for parent in sorted(touched_parents, key=lambda item: len(item.parts), reverse=True):
        current = parent
        while current != base and base in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    return {
        "restored_files": len(restored_paths),
        "removed_files": len(removed_paths),
        "snapshots": len(rows),
    }
