"""AI Agent API：后台运行、可恢复事件流与会话管理。"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.core import AgentCore
from ..agent.memory import load_memory, get_dynamic_section, clear_dynamic_section
from ..agent.run_manager import get_run_manager, TERMINAL_STATUSES
from ..agent.snapshots import restore_from_turn
from ..database import Database

router = APIRouter(prefix="/agent", tags=["agent"])
_agent: Optional[AgentCore] = None


def get_agent() -> AgentCore:
    global _agent
    if _agent is None:
        _agent = AgentCore()
    return _agent


class ChatRequest(BaseModel):
    message: str
    conversation_id: int = 0


class ConfirmRequest(BaseModel):
    conversation_id: int
    call_id: str
    approved: bool = True


class RevertRequest(BaseModel):
    conversation_id: int
    user_message_id: int


@router.post("/chat")
async def chat(req: ChatRequest):
    """创建后台 Agent run；响应返回后任务仍会继续执行。"""
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    conv_id = req.conversation_id
    async with Database() as db:
        if conv_id:
            cursor = await db.execute("SELECT id FROM conversations WHERE id = ?", (conv_id,))
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="会话不存在")
            cursor = await db.execute(
                "SELECT id FROM agent_runs WHERE conversation_id = ? AND status IN ('queued','running','waiting') LIMIT 1",
                (conv_id,),
            )
            if await cursor.fetchone():
                raise HTTPException(status_code=409, detail="当前会话已有任务正在运行")
        else:
            cursor = await db.execute(
                "INSERT INTO conversations (title) VALUES (?)", (req.message[:50],)
            )
            conv_id = cursor.lastrowid

        cursor = await db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        )
        history = [{"role": row[0], "content": row[1]} for row in await cursor.fetchall()]
        cursor = await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
            (conv_id, req.message),
        )
        user_message_id = cursor.lastrowid
        await db.commit()

    manager = get_run_manager(get_agent())
    run_id = await manager.start(
        req.message, conv_id, history, user_message_id=user_message_id
    )
    return {
        "status": "accepted",
        "run_id": run_id,
        "conversation_id": conv_id,
        "user_message_id": user_message_id,
    }


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, after: int = Query(default=0, ge=0)):
    """重放并持续推送 run 事件；观察连接断开不会取消后台任务。"""
    async with Database() as db:
        cursor = await db.execute("SELECT id FROM agent_runs WHERE id = ?", (run_id,))
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="运行不存在")

    async def event_generator():
        event_id = after
        idle_ticks = 0
        while True:
            async with Database() as db:
                cursor = await db.execute(
                    "SELECT id, event_json FROM agent_run_events WHERE run_id = ? AND id > ? ORDER BY id",
                    (run_id, event_id),
                )
                rows = await cursor.fetchall()
                status_cursor = await db.execute(
                    "SELECT status, error FROM agent_runs WHERE id = ?", (run_id,)
                )
                run = await status_cursor.fetchone()

            for row in rows:
                event_id = row[0]
                payload = json.loads(row[1])
                payload["event_id"] = event_id
                yield f"id: {event_id}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

            if not run:
                break
            if run[0] in TERMINAL_STATUSES and not rows:
                break

            idle_ticks += 1
            if idle_ticks % 40 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    async with Database() as db:
        cursor = await db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="运行不存在")
    return dict(row)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    cancelled = await get_run_manager(get_agent()).cancel(run_id)
    if not cancelled:
        async with Database() as db:
            cursor = await db.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,))
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="运行不存在")
    return {"status": "cancelled" if cancelled else row[0]}


@router.post("/confirm")
async def confirm_tool(req: ConfirmRequest):
    resolved = get_run_manager(get_agent()).confirm(req.call_id, req.approved)
    if not resolved:
        raise HTTPException(status_code=404, detail="确认请求已失效")
    return {"status": "approved" if req.approved else "rejected"}


@router.get("/conversations")
async def list_conversations():
    async with Database() as db:
        cursor = await db.execute(
            """SELECT c.*,
                      (SELECT status FROM agent_runs r WHERE r.conversation_id = c.id
                       ORDER BY r.created_at DESC LIMIT 1) AS run_status
               FROM conversations c ORDER BY c.created_at DESC LIMIT 50"""
        )
        rows = await cursor.fetchall()
    return {"conversations": [dict(row) for row in rows]}


@router.post("/conversations")
async def create_conversation(title: str = ""):
    async with Database() as db:
        cursor = await db.execute("INSERT INTO conversations (title) VALUES (?)", (title,))
        await db.commit()
        return {"id": cursor.lastrowid, "title": title}


@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: int):
    async with Database() as db:
        cursor = await db.execute(
            """SELECT m.*,
                      CASE WHEN m.role = 'user' AND EXISTS (
                          SELECT 1 FROM agent_runs r WHERE r.user_message_id = m.id
                      ) THEN 1 ELSE 0 END AS can_revert
               FROM messages m WHERE m.conversation_id = ? ORDER BY m.id""",
            (conv_id,),
        )
        rows = await cursor.fetchall()
    return {"messages": [dict(row) for row in rows]}


@router.get("/conversations/{conv_id}/active-run")
async def get_active_run(conv_id: int):
    async with Database() as db:
        cursor = await db.execute(
            """SELECT * FROM agent_runs WHERE conversation_id = ?
               AND status IN ('queued','running','waiting') ORDER BY created_at DESC LIMIT 1""",
            (conv_id,),
        )
        row = await cursor.fetchone()
    return {"run": dict(row) if row else None}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: int):
    async with Database() as db:
        cursor = await db.execute(
            "SELECT id FROM agent_runs WHERE conversation_id = ? AND status IN ('queued','running','waiting')",
            (conv_id,),
        )
        active_run_ids = [row[0] for row in await cursor.fetchall()]
    manager = get_run_manager(get_agent())
    for run_id in active_run_ids:
        await manager.cancel(run_id)
    async with Database() as db:
        cursor = await db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        await db.commit()
    if not cursor.rowcount:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "deleted", "id": conv_id}


@router.post("/revert")
async def revert(req: RevertRequest):
    """回到指定用户轮次之前，同时恢复该轮及后续轮次改动的文件。"""
    async with Database() as db:
        cursor = await db.execute(
            """SELECT id, created_at FROM messages
               WHERE id = ? AND conversation_id = ? AND role = 'user'""",
            (req.user_message_id, req.conversation_id),
        )
        target = await cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="要回退的对话轮次不存在")
        cursor = await db.execute(
            """SELECT id FROM agent_runs WHERE conversation_id = ?
               AND status IN ('queued','running','waiting') LIMIT 1""",
            (req.conversation_id,),
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="Agent 仍在运行，请先等待完成或停止任务")

    try:
        file_result = await restore_from_turn(
            req.conversation_id, req.user_message_id
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"恢复文件失败：{exc}") from exc

    async with Database() as db:
        # 新版 run 直接按消息关联；旧版记录则以创建时间兼容清理。
        await db.execute(
            """DELETE FROM agent_runs
               WHERE conversation_id = ? AND (
                   user_message_id >= ? OR
                   (user_message_id IS NULL AND created_at >= ?)
               )""",
            (req.conversation_id, req.user_message_id, target[1]),
        )
        cursor = await db.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
            (req.conversation_id, req.user_message_id),
        )
        removed_messages = cursor.rowcount
        await db.execute(
            "DELETE FROM snapshots WHERE conversation_id = ? AND turn_index >= ?",
            (req.conversation_id, req.user_message_id),
        )
        cursor = await db.execute(
            """SELECT content FROM messages
               WHERE conversation_id = ? AND role = 'user' ORDER BY id LIMIT 1""",
            (req.conversation_id,),
        )
        first_message = await cursor.fetchone()
        await db.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            ((first_message[0][:50] if first_message else ""), req.conversation_id),
        )
        await db.commit()
    return {
        "status": "ok",
        "reverted_to": req.user_message_id,
        "removed_messages": removed_messages,
        **file_result,
    }


@router.get("/memory")
async def get_memory():
    return {"full": load_memory(), "dynamic": get_dynamic_section()}


@router.post("/memory/clear")
async def clear_memory():
    clear_dynamic_section()
    return {"status": "ok"}
