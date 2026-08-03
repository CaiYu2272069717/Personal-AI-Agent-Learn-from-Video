"""AI Agent API 路由"""

import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List

from ..agent.core import AgentCore
from ..agent.memory import load_memory, get_dynamic_section, update_dynamic_section, clear_dynamic_section
from ..database import Database

router = APIRouter(prefix="/agent", tags=["agent"])

# 全局 Agent 实例
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


@router.post("/chat")
async def chat(req: ChatRequest):
    """Agent 对话（SSE 流式）"""
    agent = get_agent()

    # 获取或创建会话
    conv_id = req.conversation_id
    if conv_id == 0:
        async with Database() as db:
            cursor = await db.execute(
                "INSERT INTO conversations (title) VALUES (?)",
                (req.message[:50],)
            )
            await db.commit()
            conv_id = cursor.lastrowid

    # 保存用户消息
    async with Database() as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
            (conv_id, req.message)
        )
        await db.commit()

    # 加载历史
    history = []
    async with Database() as db:
        cursor = await db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,)
        )
        rows = await cursor.fetchall()
        for row in rows[:-1]:  # 排除刚插入的当前消息
            history.append({"role": row[0], "content": row[1]})

    async def stream():
        async for event in agent.chat(req.message, conv_id, history):
            yield event

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/conversations")
async def list_conversations():
    """会话列表"""
    async with Database() as db:
        cursor = await db.execute(
            "SELECT * FROM conversations ORDER BY created_at DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
    return {"conversations": [dict(r) for r in rows]}


@router.post("/conversations")
async def create_conversation(title: str = ""):
    """创建新会话"""
    async with Database() as db:
        cursor = await db.execute(
            "INSERT INTO conversations (title) VALUES (?)", (title,)
        )
        await db.commit()
        return {"id": cursor.lastrowid, "title": title}


@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: int):
    """获取会话消息"""
    async with Database() as db:
        cursor = await db.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,)
        )
        rows = await cursor.fetchall()
    return {"messages": [dict(r) for r in rows]}


@router.post("/revert")
async def revert(conversation_id: int = 0, turn_index: int = 0):
    """回退到指定轮"""
    # TODO: 完整实现快照恢复
    async with Database() as db:
        # 获取该轮之后的消息并删除
        cursor = await db.execute(
            "SELECT id FROM messages WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,)
        )
        all_msgs = await cursor.fetchall()
        if turn_index < len(all_msgs):
            ids_to_remove = [all_msgs[i][0] for i in range(turn_index, len(all_msgs))]
            for msg_id in ids_to_remove:
                await db.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
            await db.commit()

    return {"status": "ok", "reverted_to": turn_index}


@router.get("/memory")
async def get_memory():
    """获取 Agent 记忆"""
    return {
        "full": load_memory(),
        "dynamic": get_dynamic_section(),
    }


@router.post("/memory/clear")
async def clear_memory():
    """清空可变区记忆"""
    clear_dynamic_section()
    return {"status": "ok"}
