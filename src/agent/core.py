"""Agent 核心：tool-call 循环 + 权限确认 + SSE 流式输出"""

import json
import re
import asyncio
from typing import AsyncGenerator, Optional, List, Dict, Any
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from ..config import get_config
from .tools import ToolRegistry, infer_explicit_tool_call, infer_explicit_tool_name
from .permissions import PermissionManager
from .memory import load_memory
from ..database import Database
from .skill_loader import SkillManager


MAX_CONTEXT_TOKENS = 6000  # 上下文窗口保留 token 上限（粗估）

# 某些模型（如 GLM）会调用自己内置的工具名而非用户注册的工具名。
# 这里做一层映射，将模型内置别名路由到我们已注册的工具。
_MAP_BUILTIN_TOOLS = {
    "search": "web_search",         # GLM 内置搜索 → 我们的 web_search
    "web_browser": "web_fetch",     # 某些模型的浏览器 → web_fetch
    "code_interpreter": "run_python_sandbox",  # 代码解释器 → 沙箱
}


# 匹配模型输出中的引用标记：
# 英文方括号: [turn0search0], [turn1search2result0], [turn0source0], [turn1fetch0], [search_result_1], [ref1]
# 中文方括号: 【turn0search1】【turn1fetch0】【4†source】【0:1†source】
_CITATION_PATTERN = re.compile(
    r'\[turn\d+search\d+(?:result\d+)?\]'
    r'|\[turn\d+source\d+\]'
    r'|\[turn\d+fetch\d+\]'
    r'|\[search_result_\d+\]'
    r'|\[ref\d+\]'
    r'|\u3010turn\d+search\d+(?:result\d+)?\u3011'
    r'|\u3010turn\d+source\d+\u3011'
    r'|\u3010turn\d+fetch\d+\u3011'
    r'|\u3010[^\u3011]*?\u2020[^\u3011]*?\u3011'
)


def _clean_citations(text: str) -> str:
    """清除模型输出中的原始引用标记"""
    if not text:
        return text
    return _CITATION_PATTERN.sub('', text)


class _StreamCitationFilter:
    """流式引用标记过滤器
    
    处理跨 chunk 的引用标记。缓冲可能不完整的标记，
    确保完整标记被清除，不完整内容延迟输出。
    """
    def __init__(self):
        self._buffer = ""
    
    def feed(self, delta: str) -> str:
        """输入增量文本，返回可安全输出的清理后文本"""
        self._buffer += delta
        
        # 查找最后一个可能未闭合的 [ 或 【
        last_bracket = self._buffer.rfind('[')
        last_cn_bracket = self._buffer.rfind('\u3010')
        cut_pos = max(last_bracket, last_cn_bracket)
        
        if cut_pos >= 0:
            # 检查该括号是否已闭合
            after = self._buffer[cut_pos:]
            if ('[' in after and ']' not in after) or ('\u3010' in after and '\u3011' not in after):
                # 未闭合 - 保留在缓冲区
                output = self._buffer[:cut_pos]
                self._buffer = self._buffer[cut_pos:]
            else:
                # 已闭合 - 全部输出
                output = self._buffer
                self._buffer = ""
        else:
            output = self._buffer
            self._buffer = ""
        
        return _clean_citations(output)
    
    def flush(self) -> str:
        """刷新缓冲区中的剩余内容"""
        output = _clean_citations(self._buffer)
        self._buffer = ""
        return output


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
        self._skill_manager = None  # SkillManager 实例
        self._pending_confirmations: Dict[str, asyncio.Future] = {}

    def set_skill_manager(self, skill_manager):
        """注入 Skill 管理器，用于自动匹配 Skill 上下文"""
        self._skill_manager = skill_manager

    def resolve_confirmation(self, call_id: str, approved: bool) -> bool:
        """响应一个等待中的工具确认。"""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"resolve_confirmation called: call_id={call_id!r}, pending={list(self._pending_confirmations.keys())}")

        waiter = self._pending_confirmations.get(call_id)
        if not waiter or waiter.done():
            # Fallback: if there's exactly one pending confirmation, resolve it
            # (handles call_id mismatch from models that generate different IDs)
            active = [(k, w) for k, w in self._pending_confirmations.items() if not w.done()]
            if len(active) == 1:
                logger.info(f"call_id mismatch, resolving sole pending: {active[0][0]}")
                active[0][1].set_result(approved)
                return True
            logger.warning(f"confirm failed: call_id={call_id!r} not found in pending")
            return False
        waiter.set_result(approved)
        return True

    def auto_approve_all_pending(self):
        """当用户开启完全访问时，自动批准所有待确认的工具请求。"""
        import logging
        logger = logging.getLogger(__name__)
        active = [(k, w) for k, w in self._pending_confirmations.items() if not w.done()]
        if active:
            logger.info(f"full access enabled, auto-approving {len(active)} pending confirmations")
        for call_id, waiter in active:
            try:
                waiter.set_result(True)
            except Exception:
                pass

    def _get_client(self) -> AsyncOpenAI:
        """获取 Agent LLM 客户端"""
        config = get_config().agent_llm
        return AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
        )

    def _parse_slash_command(self, user_message: str) -> tuple:
        """解析 /skill-name 斜杠命令前缀
        
        Returns:
            (skill_name_or_none, actual_message)
        """
        import re
        match = re.match(r"^/([a-zA-Z0-9_-]+)\s*(.*)", user_message, re.DOTALL)
        if match and self._skill_manager:
            skill_name = match.group(1)
            skill = self._skill_manager.get_skill(skill_name)
            if skill:
                return skill_name, match.group(2).strip() or user_message
        return None, user_message

    def _build_system_prompt(self, user_message: str = "") -> str:
        """构建系统提示词（含记忆注入 + 自动匹配 Skill 上下文）"""
        memory = load_memory()

        # 自动匹配 Skill 并注入指令
        skill_context = ""
        if self._skill_manager and user_message:
            # 优先识别 /skill-name 斜杠命令
            slash_skill, _ = self._parse_slash_command(user_message)
            if slash_skill:
                skill = self._skill_manager.get_skill(slash_skill)
                if skill:
                    skill_context = self._skill_manager.build_skill_context([skill])
            else:
                matched = self._skill_manager.match_skills(user_message)
                if matched:
                    skill_context = self._skill_manager.build_skill_context(matched)

        permission_note = (
            "用户已开启「允许完全访问」，中/高危工具可直接调用，无需再次确认。"
            if get_config().agent_permission.full_access
            else "中/高危操作需等待用户确认。"
        )

        return f"""{memory}{skill_context}

---

## 当前可用工具
你可以使用以下工具来完成任务。使用 function calling 来调用工具。
工具列表已通过 tools 参数提供。

**工具使用须知（重要）**:
- 联网搜索必须调用 `web_search` 工具，不要假设环境缺少搜索能力，也不要用模型先验知识代替实时搜索
- 抓取网页正文必须调用 `web_fetch` 工具
- 知识库检索必须调用 `search_knowledge` 或 `get_item` 工具
- 当用户要求执行某个动作（写入/编辑/命令）时，应直接调用对应工具，不要仅在文本中描述意图

## 工作原则
- 回答基于知识库内容时，必须标注来源，格式如：`来源：条目《标题》(ID: N)` 或 `条目 ID: N`
- 联网搜索结果须标注来源 URL，格式如：`来源：https://...`
- 抓取网页内容后须在回答中保留来源 URL
- 不确定就说不知道，不编造
- {permission_note}

## 输出规范
- 当用户要求"步骤"、"方法"、"流程"、"方案"等多要点内容时，使用数字编号（1. 2. 3.）或"第一/第二/第三"明确组织
- 回答应充分展开，避免过简；纯文本回答时不要调用任何工具
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

        # 解析 /skill-name 前缀，将实际消息（去掉前缀）发给模型
        _, actual_message = self._parse_slash_command(user_message)

        # 构建消息历史（含 Skill 上下文注入）
        messages = [{"role": "system", "content": self._build_system_prompt(user_message)}]

        if history:
            messages.extend(history)

        messages.append({"role": "user", "content": actual_message})

        # 上下文窗口管理：截断过长历史
        messages = _truncate_history(messages, MAX_CONTEXT_TOKENS)

        tools = self.tool_registry.list_tools()
        explicit_tool = infer_explicit_tool_name(user_message)
        explicit_call = infer_explicit_tool_call(user_message)

        # 对参数完整的显式动作做确定性预执行，再让模型基于结果回答。
        # 这同时作为 OpenAI 兼容代理忽略 tool_choice 时的兜底。
        if explicit_call:
            tool_name, args = explicit_call
            call_id = f"explicit-{conversation_id}-{user_message_id or 'turn'}"
            tool_def = self.tool_registry.get_tool(tool_name)
            risk = tool_def.risk_level if tool_def else "safe"
            allowed, reason = self.permission_mgr.check_tool_permission(tool_name, risk, args)
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(args, ensure_ascii=False)},
                }],
            })
            if not allowed and "永久拦截" in (reason or ""):
                tool_result = json.dumps({"error": reason}, ensure_ascii=False)
                yield {"type": "tool_blocked", "tool": tool_name, "reason": reason}
            else:
                approved = allowed
                confirm_timeout = False
                if not allowed:
                    waiter = asyncio.get_running_loop().create_future()
                    self._pending_confirmations[call_id] = waiter
                    yield {
                        "type": "confirm_needed", "call_id": call_id,
                        "tool": tool_name, "args": args, "reason": reason,
                    }
                    try:
                        approved = await asyncio.wait_for(waiter, timeout=300)
                    except asyncio.TimeoutError:
                        approved = False
                        confirm_timeout = True
                    finally:
                        self._pending_confirmations.pop(call_id, None)
                if approved:
                    yield {"type": "tool_call", "call_id": call_id, "tool": tool_name, "args": args}
                    tool_result = await self.tool_registry.execute(
                        tool_name, args, conversation_id, user_message_id
                    )
                elif confirm_timeout:
                    tool_result = json.dumps({"error": "操作确认超时（5 分钟未收到回应），已取消"}, ensure_ascii=False)
                else:
                    tool_result = json.dumps({"error": "用户拒绝执行此工具"}, ensure_ascii=False)
                yield {
                    "type": "tool_result", "call_id": call_id, "tool": tool_name,
                    "result": tool_result[:2000], "rejected": not approved,
                }
            messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_result})
            explicit_tool = None

        # Tool-call 循环
        max_rounds = max(1, min(config.max_tool_rounds or 10, 50))
        complete_text = ""
        for round_idx in range(max_rounds):
            yield {"type": "status", "status": "thinking", "label": "正在思考"}
            try:
                request_kwargs = {
                    "model": config.model,
                    "messages": messages,
                    "tools": tools if tools else None,
                    "temperature": config.temperature,
                    "stream": True,
                }
                # 用户明确要求某个动作时，首轮只暴露目标工具并强制调用。
                # 部分 OpenAI 兼容代理在“全量工具 + 指定函数”模式下会忽略 tool_choice；
                # 收窄首轮工具集合可稳定兼容这类代理。后续轮次恢复全量工具。
                if round_idx == 0 and explicit_tool:
                    selected_tools = [
                        item for item in tools
                        if item["function"]["name"] == explicit_tool
                    ]
                    if selected_tools:
                        request_kwargs["tools"] = selected_tools
                        request_kwargs["tool_choice"] = "required"
                response = await client.chat.completions.create(**request_kwargs)
            except Exception as e:
                yield {"type": "error", "content": str(e)}
                return

            # 流式处理响应
            full_content = ""
            thinking_content = ""
            in_thinking = False
            tool_calls_buffer = []
            citation_filter = _StreamCitationFilter()

            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue

                # 思考/推理内容（部分模型如 deepseek/glm 通过 reasoning_content 字段返回）
                reasoning_text = getattr(delta, 'reasoning_content', None) or getattr(delta, 'thinking', None)
                if reasoning_text:
                    if not in_thinking:
                        in_thinking = True
                        yield {"type": "thinking", "content": ""}
                    thinking_content += reasoning_text
                    yield {"type": "thinking", "content": reasoning_text}

                # 文本内容
                if delta.content:
                    # 如果之前在思考状态，先发送 thinking_done
                    if in_thinking:
                        in_thinking = False
                        yield {"type": "thinking_done", "content": ""}
                    # 通过流式过滤器清除引用标记（处理跨 chunk 的情况）
                    cleaned = citation_filter.feed(delta.content)
                    if cleaned:
                        full_content += cleaned
                        yield {"type": "text", "content": cleaned}

                # 工具调用
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        while len(tool_calls_buffer) <= idx:
                            tool_calls_buffer.append({"id": "", "function": {"name": "", "arguments": ""}})
                        if tc.id:
                            tool_calls_buffer[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_buffer[idx]["function"]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_buffer[idx]["function"]["arguments"] += tc.function.arguments

            # 刷新 citation filter 缓冲区中的剩余内容
            remaining = citation_filter.flush()
            if remaining:
                full_content += remaining
                yield {"type": "text", "content": remaining}

            # 思考结束但没有正文内容的情况
            if in_thinking:
                yield {"type": "thinking_done", "content": ""}

            # Ensure every tool call has a valid id (Gemini may not provide one)
            for i, tc in enumerate(tool_calls_buffer):
                if not tc["id"]:
                    tc["id"] = f"call_{conversation_id}_{round_idx}_{i}"

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

                # 模型内置工具别名映射（如 GLM 的 search → web_search）
                func_name = _MAP_BUILTIN_TOOLS.get(func_name, func_name)
                tc["function"]["name"] = func_name

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
                confirm_timeout = False
                try:
                    approved = await asyncio.wait_for(waiter, timeout=300)
                except asyncio.TimeoutError:
                    approved = False
                    confirm_timeout = True
                finally:
                    self._pending_confirmations.pop(entry["call_id"], None)
                if approved:
                    yield {"type": "tool_call", "call_id": entry["call_id"], "tool": entry["name"], "args": entry["args"]}
                    tool_result = await self.tool_registry.execute(
                        entry["name"], entry["args"], conversation_id, user_message_id
                    )
                elif confirm_timeout:
                    tool_result = json.dumps({"error": "操作确认超时（5 分钟未收到回应），已取消"}, ensure_ascii=False)
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
        """保存消息到数据库（直接流模式 conversation_id=0 时跳过）"""
        if not conversation_id:
            return
        async with Database() as db:
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, tool_calls_json) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, tool_calls_json)
            )
            await db.commit()
