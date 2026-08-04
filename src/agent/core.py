"""Agent 核心：tool-call 循环 + 权限确认 + SSE 流式输出"""

import json
import asyncio
from typing import AsyncGenerator, Optional, List, Dict, Any
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from ..config import get_config
from .tools import ToolRegistry
from .permissions import PermissionManager
from .memory import load_memory
from ..database import Database


MAX_CONTEXT_TOKENS = 6000  # 上下文窗口保留 token 上限（粗估）


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文≈1.5字/token, 英文≈4字符/token）"""
    if not text:
        return 0
    # 简单混合估算
    cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - cn_chars
    return int(cn_chars * 0.7 + other_chars / 3.5)


def _truncate_history(messages: list, max_tokens: int) -> list:
    """截断历史消息以控制上下文窗口大小
    
    保留策略：始终保留 system prompt + 最近 N 条消息
    """
    if len(messages) <= 2:
        return messages
    
    system = messages[0]  # system prompt 始终保留
    rest = messages[1:]
    
    # 从后往前累加 token，直到超出预算
    total = _estimate_tokens(system.get("content", "") if isinstance(system, dict) else "")
    keep_from = 0
    
    for i in range(len(rest) - 1, -1, -1):
        msg = rest[i]
        content = msg.get("content", "") or ""
        tokens = _estimate_tokens(content)
        if total + tokens > max_tokens:
            keep_from = i + 1
            break
        total += tokens
    
    truncated = [system] + rest[keep_from:]
    
    # 如果截断了，在开头加一条摘要提示
    if keep_from > 0:
        truncated.insert(1, {
            "role": "system",
            "content": f"[注意：早期 {keep_from} 条消息已被截断以控制上下文长度]",
        })
    
    return truncated


@dataclass
class AgentMessage:
    """Agent 消息"""
    role: str
    content: str = ""
    tool_calls: list = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""


class AgentCore:
    """Agent 核心引擎"""

    def __init__(self):
        self.tool_registry = ToolRegistry()
        self.permission_mgr = PermissionManager()
        self._pending_confirmations: Dict[str, asyncio.Future] = {}

    def resolve_confirmation(self, call_id: str, approved: bool) -> bool:
        """响应一个等待中的工具确认。"""
        waiter = self._pending_confirmations.get(call_id)
        if not waiter or waiter.done():
            return False
        waiter.set_result(approved)
        return True

    def _get_client(self) -> AsyncOpenAI:
        """获取 Agent LLM 客户端"""
        config = get_config().agent_llm
        return AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )

    def _build_system_prompt(self) -> str:
        """构建系统提示词（含记忆注入）"""
        memory = load_memory()
        return f"""{memory}

---

## 当前可用工具
你可以使用以下工具来完成任务。使用 function calling 来调用工具。
工具列表已通过 tools 参数提供。

## 工作原则
- 回答基于知识库内容时，标注来源（条目标题/ID）
- 联网搜索结果须标注来源 URL
- 不确定就说不知道，不编造
- 中/高危操作需等待用户确认
"""

    async def chat(
        self,
        user_message: str,
        conversation_id: int,
        history: Optional[List[Dict]] = None,
        user_message_id: int = 0,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Agent 对话（流式输出）

        Yields:
            可持久化的结构化事件
        """
        config = get_config().agent_llm
        client = self._get_client()

        # 构建消息历史
        messages = [{"role": "system", "content": self._build_system_prompt()}]

        if history:
            messages.extend(history)

        messages.append({"role": "user", "content": user_message})

        # 上下文窗口管理：截断过长历史
        messages = _truncate_history(messages, MAX_CONTEXT_TOKENS)

        tools = self.tool_registry.list_tools()

        # Tool-call 循环
        max_rounds = max(1, min(config.max_tool_rounds, 50))
        complete_text = ""
        for round_idx in range(max_rounds):
            yield {"type": "status", "status": "thinking", "label": "正在思考"}
            try:
                response = await client.chat.completions.create(
                    model=config.model,
                    messages=messages,
                    tools=tools if tools else None,
                    temperature=config.temperature,
                    stream=True,
                )
            except Exception as e:
                yield {"type": "error", "content": str(e)}
                return

            # 流式处理响应
            full_content = ""
            tool_calls_buffer = []

            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # 文本内容
                if delta.content:
                    full_content += delta.content
                    yield {"type": "text", "content": delta.content}

                # 工具调用
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        while len(tool_calls_buffer) <= idx:
                            tool_calls_buffer.append({"id": "", "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            tool_calls_buffer[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_buffer[idx]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments

            # 检查是否有工具调用
            if not tool_calls_buffer:
                # 纯文本回复，结束
                complete_text += full_content
                # 保存消息
                await self._save_message(conversation_id, "assistant", complete_text)
                yield {"type": "done", "content": complete_text, "conversation_id": conversation_id}
                return

            complete_text += full_content

            # 处理工具调用
            messages.append({
                "role": "assistant",
                "content": full_content or None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function", "function": tc["function"]}
                    for tc in tool_calls_buffer
                ],
            })

            # 分类工具调用：可并发执行 vs 需要确认
            safe_calls = []
            confirm_calls = []
            blocked_calls = []

            for tc in tool_calls_buffer:
                call_id = tc["id"]
                func_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}

                tool_def = self.tool_registry.get_tool(func_name)
                risk = tool_def.risk_level if tool_def else "safe"
                allowed, reason = self.permission_mgr.check_tool_permission(
                    func_name, risk, args
                )

                entry = {"call_id": call_id, "name": func_name, "args": args, "reason": reason}

                if not allowed and "永久拦截" in (reason or ""):
                    blocked_calls.append(entry)
                elif not allowed:
                    confirm_calls.append(entry)
                else:
                    safe_calls.append(entry)

            # 处理黑名单拦截
            for entry in blocked_calls:
                tool_result = json.dumps({"error": entry["reason"]}, ensure_ascii=False)
                yield {"type": "tool_blocked", "tool": entry["name"], "reason": entry["reason"]}
                messages.append({"role": "tool", "tool_call_id": entry["call_id"], "content": tool_result})

            # 并发执行安全工具
            if safe_calls:
                for entry in safe_calls:
                    yield {"type": "tool_call", "call_id": entry["call_id"], "tool": entry["name"], "args": entry["args"]}

                # 并发执行
                results = await asyncio.gather(
                    *[
                        self.tool_registry.execute(
                            e["name"], e["args"], conversation_id, user_message_id
                        )
                        for e in safe_calls
                    ],
                    return_exceptions=True,
                )

                for entry, result in zip(safe_calls, results):
                    if isinstance(result, Exception):
                        tool_result = json.dumps({"error": str(result)}, ensure_ascii=False)
                    else:
                        tool_result = result
                    yield {"type": "tool_result", "call_id": entry["call_id"], "tool": entry["name"], "result": tool_result[:2000]}
                    messages.append({"role": "tool", "tool_call_id": entry["call_id"], "content": tool_result})

            # 需要确认的工具（顺序执行，等待确认）
            for entry in confirm_calls:
                waiter = asyncio.get_running_loop().create_future()
                self._pending_confirmations[entry["call_id"]] = waiter
                yield {
                    "type": "confirm_needed", "call_id": entry["call_id"],
                    "tool": entry["name"], "args": entry["args"], "reason": entry["reason"],
                }
                try:
                    approved = await waiter
                finally:
                    self._pending_confirmations.pop(entry["call_id"], None)
                if approved:
                    yield {"type": "tool_call", "call_id": entry["call_id"], "tool": entry["name"], "args": entry["args"]}
                    tool_result = await self.tool_registry.execute(
                        entry["name"], entry["args"], conversation_id, user_message_id
                    )
                else:
                    tool_result = json.dumps({"error": "用户拒绝执行此工具"}, ensure_ascii=False)
                yield {
                    "type": "tool_result", "call_id": entry["call_id"], "tool": entry["name"],
                    "result": tool_result[:2000], "rejected": not approved,
                }
                messages.append({"role": "tool", "tool_call_id": entry["call_id"], "content": tool_result})

        # 超过最大轮数
        yield {"type": "error", "content": "工具调用轮数超限，请简化请求"}

    async def _save_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        tool_calls_json: str = "",
    ):
        """保存消息到数据库"""
        async with Database() as db:
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_calls_json) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, tool_calls_json)
            )
            await db.commit()
