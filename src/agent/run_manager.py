"""与浏览器连接解耦的 Agent 后台运行管理器。"""

import asyncio
import json
import uuid
from contextlib import suppress
from typing import Dict, Optional

from .core import AgentCore
from ..database import Database


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class AgentRunManager:
    def __init__(self, agent: AgentCore):
        self.agent = agent
        self._tasks: Dict[str, asyncio.Task] = {}

    async def start(
        self, message: str, conversation_id: int, history: list, user_message_id: int = 0
    ) -> str:
        run_id = uuid.uuid4().hex
        async with Database() as db:
            await db.execute(
                """INSERT INTO agent_runs
                       (id, conversation_id, user_message_id, status)
                   VALUES (?, ?, ?, 'queued')""",
                (run_id, conversation_id, user_message_id or None),
            )
            await db.commit()
        task = asyncio.create_task(
            self._run(run_id, message, conversation_id, history, user_message_id),
            name=f"agent-run-{run_id[:8]}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
        return run_id

    async def _set_status(self, run_id: str, status: str, error: str = ""):
        async with Database() as db:
            await db.execute(
                "UPDATE agent_runs SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error, run_id),
            )
            await db.commit()

    async def _record_event(self, run_id: str, event: dict):
        async with Database() as db:
            await db.execute(
                "INSERT INTO agent_run_events (run_id, event_json) VALUES (?, ?)",
                (run_id, json.dumps(event, ensure_ascii=False)),
            )
            await db.commit()

    async def _record_terminal_event(
        self, run_id: str, event: dict, status: str, error: str = ""
    ):
        """原子写入终止事件和状态，避免前端先看到完成但后端仍判定运行中。"""
        async with Database() as db:
            await db.execute(
                "INSERT INTO agent_run_events (run_id, event_json) VALUES (?, ?)",
                (run_id, json.dumps(event, ensure_ascii=False)),
            )
            await db.execute(
                "UPDATE agent_runs SET status = ?, error = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error, run_id),
            )
            await db.commit()

    async def _run(
        self, run_id: str, message: str, conversation_id: int,
        history: list, user_message_id: int,
    ):
        await self._set_status(run_id, "running")
        text_buffer = ""
        last_flush = asyncio.get_running_loop().time()
        terminal_event = ""

        async def flush_text():
            nonlocal text_buffer, last_flush
            if text_buffer:
                # Split large buffers into smaller chunks for progressive UI rendering.
                # Models like Gemini return content in large blocks; splitting ensures
                # the frontend event stream receives progressive updates.
                chunk_size = 80
                if len(text_buffer) > chunk_size * 2:
                    while text_buffer:
                        chunk = text_buffer[:chunk_size]
                        text_buffer = text_buffer[chunk_size:]
                        await self._record_event(run_id, {"type": "text", "content": chunk})
                        if text_buffer:
                            await asyncio.sleep(0.02)
                else:
                    await self._record_event(run_id, {"type": "text", "content": text_buffer})
                    text_buffer = ""
                last_flush = asyncio.get_running_loop().time()

        try:
            async for event in self.agent.chat(
                message, conversation_id, history, user_message_id=user_message_id
            ):
                if event.get("type") == "text":
                    text_buffer += event.get("content", "")
                    now = asyncio.get_running_loop().time()
                    if len(text_buffer) < 48 and now - last_flush < 0.12:
                        continue
                    await flush_text()
                    continue
                await flush_text()
                if event.get("type") == "confirm_needed":
                    await self._set_status(run_id, "waiting")
                elif event.get("type") in {"text", "tool_call", "tool_result", "status"}:
                    await self._set_status(run_id, "running")
                if event.get("type") in {"done", "error"}:
                    terminal_event = event["type"]
                    status = "completed" if terminal_event == "done" else "failed"
                    error = "" if terminal_event == "done" else event.get("content", "Agent 运行失败")
                    await self._record_terminal_event(run_id, event, status, error)
                else:
                    await self._record_event(run_id, event)
            await flush_text()
            if not terminal_event:
                await self._set_status(run_id, "completed")
        except asyncio.CancelledError:
            await self._record_terminal_event(
                run_id, {"type": "cancelled", "content": "已停止生成"}, "cancelled"
            )
            raise
        except Exception as exc:
            await self._record_terminal_event(
                run_id, {"type": "error", "content": str(exc)}, "failed", str(exc)
            )

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if not task or task.done():
            return False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    def confirm(self, call_id: str, approved: bool) -> bool:
        return self.agent.resolve_confirmation(call_id, approved)

    async def shutdown(self):
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_manager: Optional[AgentRunManager] = None


def get_run_manager(agent: AgentCore) -> AgentRunManager:
    global _manager
    if _manager is None or _manager.agent is not agent:
        _manager = AgentRunManager(agent)
    return _manager
