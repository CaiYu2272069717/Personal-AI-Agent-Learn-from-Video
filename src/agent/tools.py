"""Agent 工具注册表

所有 Agent 可调用的工具定义与实现。
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass

from ..config import get_config, WORKDIR, BASE_DIR
from .snapshots import capture_project_state, record_file_before, record_project_changes


def infer_explicit_tool_name(user_message: str) -> Optional[str]:
    """从用户明确的动作词推断首轮工具。

    只处理高置信度、直接指向单一工具的请求；普通问题返回 None，
    继续交给模型自主选择，避免把路由器变成僵化的关键词分类器。
    """
    text = (user_message or "").strip().lower()
    if not text:
        return None

    # 高危/写入动作优先，避免被“读取/查看”类词覆盖。
    if any(word in text for word in ("写入", "写文件", "创建文件", "新建文件")):
        return "write_file"
    if any(word in text for word in ("编辑文件", "修改文件", "替换文件内容")):
        return "edit_file"
    if any(word in text for word in ("执行命令", "运行命令", "执行 python", "运行 python", "shell 命令")):
        return "run_command"

    # 明确的图片识别/沙箱请求。
    if any(word in text for word in ("ocr", "识别图片", "图片中的文字", "图片文字")) or (
        "识别" in text and any(ext in text for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"))
    ):
        return "ocr_image"
    if any(word in text for word in ("python 沙箱", "受限 python", "沙箱中", "用沙箱计算", "沙箱计算")):
        return "run_python_sandbox"

    # 知识库操作必须优先于通用联网搜索。
    if any(word in text for word in ("知识库条目", "知识库中搜索", "搜索知识库", "检索知识库", "知识库内容")):
        if any(word in text for word in ("读取", "获取", "完整内容")) or ("条目" in text and any(char.isdigit() for char in text)):
            return "get_item"
        return "search_knowledge"

    # 指定本地文件/目录工具。
    if (
        any(word in text for word in ("列出目录", "列出文件夹", "目录内容", "文件夹内容"))
        or ("列出" in text and "目录" in text)
    ):
        return "list_dir"
    if (
        any(word in text for word in ("读取文件", "查看文件", "打开文件"))
        or (any(word in text for word in ("读取", "查看", "打开")) and any(ext in text for ext in (".md", ".txt", ".json", ".py", ".html", ".css", ".js")))
    ):
        return "read_file"
    if (
        "glob" in text
        or (any(word in text for word in ("找出", "搜索所有")) and any(word in text for word in ("文件", "markdown", ".md", "*.")))
    ):
        return "glob_files"

    # URL 抓取优先于通用搜索；否则明确的“搜索官网”请求路由 web_search。
    has_url = "http://" in text or "https://" in text
    if has_url and any(word in text for word in ("抓取", "网页正文", "概括网页", "读取网页", "打开网页")):
        return "web_fetch"
    if any(word in text for word in ("联网搜索", "网络搜索", "网上搜索", "实时搜索")) or (
        "搜索" in text and any(word in text for word in ("官网", "官方网站", "官方站点"))
    ):
        return "web_search"

    return None


def infer_explicit_tool_call(user_message: str) -> Optional[tuple[str, Dict[str, Any]]]:
    """解析可确定参数的显式工具请求。

    这是模型 tool_choice 的确定性兜底：仅当用户已经给出足够参数时返回，
    不猜测缺失参数，也不执行开放式任务。
    """
    text = (user_message or "").strip()
    tool_name = infer_explicit_tool_name(text)
    if not tool_name:
        return None

    lower = text.lower()
    path_match = re.search(
        r"(?:[a-zA-Z]:[\\/][^\s，。；;]+|(?:workspace|temp|output|library|prompts|static|templates|src|docs)[\\/][^\s，。；;]+)",
        text,
    )
    url_match = re.search(r"https?://[^\s，。；;]+", text)

    if tool_name == "web_search":
        query = re.sub(r"^(?:请)?(?:联网|网络|网上|实时)?搜索\s*", "", text, flags=re.IGNORECASE)
        query = re.sub(r"并(?:给出|注明|保留).*$", "", query).strip(" ，。")
        return tool_name, {"query": query or text}
    if tool_name == "web_fetch" and url_match:
        return tool_name, {"url": url_match.group(0)}
    if tool_name == "search_knowledge":
        query = re.sub(r"^.*?(?:搜索|检索)", "", text).replace("知识库中", "").replace("知识库", "")
        query = re.sub(r"[，,].*$", "", query).strip(" ，。")
        return tool_name, {"query": query or text}
    if tool_name == "get_item":
        item_match = re.search(r"条目\s*(?:id\s*[:：#]?\s*)?(\d+)", lower, re.IGNORECASE)
        if item_match:
            return tool_name, {"item_id": int(item_match.group(1))}
    if tool_name == "list_dir":
        dir_match = re.search(r"列出\s+([^\s，。；;]+)\s*目录", text)
        if dir_match:
            return tool_name, {"path": dir_match.group(1)}
        if path_match:
            return tool_name, {"path": path_match.group(0).rstrip("。")}
    if tool_name == "read_file" and path_match:
        return tool_name, {"path": path_match.group(0).rstrip("。")}
    if tool_name == "glob_files":
        root = "workspace" if "workspace" in lower else "."
        pattern = "*.md" if ("markdown" in lower or "md 文件" in lower) else "*"
        return tool_name, {"path": root, "pattern": pattern}
    if tool_name == "run_python_sandbox":
        range_match = re.search(r"(\d+)\s*到\s*(\d+).*?和", text)
        if range_match:
            start, end = map(int, range_match.groups())
            return tool_name, {"code": f"print(sum(range({start}, {end + 1})))"}
    if tool_name == "write_file" and path_match:
        content_match = re.search(r"把\s+(.+?)\s+写入", text)
        if content_match:
            return tool_name, {"path": path_match.group(0).rstrip("。"), "content": content_match.group(1).strip()}
    if tool_name == "run_command":
        command = re.sub(r"^(?:请)?(?:执行|运行)命令\s*", "", text).strip(" 。")
        if command:
            return tool_name, {"command": command}
    if tool_name == "ocr_image" and path_match:
        return tool_name, {"path_or_url": path_match.group(0).rstrip("。")}
    return None


@dataclass
class ToolDef:
    """工具定义"""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    risk_level: str = "safe"  # safe / low / medium / high
    handler: Optional[Callable] = None


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}
        self._register_builtin_tools()

    def register(self, tool_def: ToolDef):
        """注册工具"""
        self._tools[tool_def.name] = tool_def

    def get_tool(self, name: str) -> Optional[ToolDef]:
        """获取工具定义"""
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        """列出所有工具（用于 function calling schema）"""
        result = []
        for tool in self._tools.values():
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            })
        return result

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        conversation_id: int = 0,
        user_message_id: int = 0,
    ) -> str:
        """执行工具"""
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        if not tool.handler:
            return json.dumps({"error": f"工具 {name} 无处理函数"}, ensure_ascii=False)

        try:
            # 精确记录文件工具；shell 命令通过项目前后状态差异捕获未知改动。
            if name in {"write_file", "edit_file"} and arguments.get("path"):
                await record_file_before(
                    conversation_id, user_message_id, arguments["path"]
                )
            command_state = None
            if name == "run_command" and conversation_id and user_message_id:
                command_state = await capture_project_state()
            result = await tool.handler(**arguments)
            if command_state is not None:
                await record_project_changes(
                    conversation_id, user_message_id, command_state
                )
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _register_builtin_tools(self):
        """注册内置工具"""

        # --- search_knowledge ---
        self.register(ToolDef(
            name="search_knowledge",
            description="检索知识库（向量+关键词混合搜索）",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或问题"},
                    "top_k": {"type": "integer", "description": "返回结果数量", "default": 5},
                },
                "required": ["query"],
            },
            risk_level="safe",
            handler=self._tool_search_knowledge,
        ))

        # --- get_item ---
        self.register(ToolDef(
            name="get_item",
            description="获取知识库中某条目的完整内容",
            parameters={
                "type": "object",
                "properties": {
                    "item_id": {"type": "integer", "description": "条目 ID"},
                },
                "required": ["item_id"],
            },
            risk_level="safe",
            handler=self._tool_get_item,
        ))

        # --- run_pipeline ---
        self.register(ToolDef(
            name="run_pipeline",
            description="调用系统流水线处理一条新链接（下载/转录/总结）",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "视频/文章链接"},
                    "template": {"type": "string", "description": "总结模板", "default": "knowledge"},
                },
                "required": ["url"],
            },
            risk_level="safe",
            handler=self._tool_run_pipeline,
        ))

        # --- web_search ---
        self.register(ToolDef(
            name="web_search",
            description='联网搜索引擎查询（类似 Google/Bing 搜索）。当用户要求「搜索」、「查找」、「查一下」网络信息，或需要实时网络数据、最新资讯时，必须使用此工具。返回结果列表（每条含标题、URL、摘要）。本环境的搜索能力由此工具提供，没有其他内置搜索。',
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
            risk_level="safe",
            handler=self._tool_web_search,
        ))

        # --- web_fetch ---
        self.register(ToolDef(
            name="web_fetch",
            description="抓取网页正文转 Markdown",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "网页 URL"},
                },
                "required": ["url"],
            },
            risk_level="safe",
            handler=self._tool_web_fetch,
        ))

        # --- read_file ---
        self.register(ToolDef(
            name="read_file",
            description="读取本地文件内容",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                },
                "required": ["path"],
            },
            risk_level="low",
            handler=self._tool_read_file,
        ))

        # --- list_dir ---
        self.register(ToolDef(
            name="list_dir",
            description="列出目录内容",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径"},
                },
                "required": ["path"],
            },
            risk_level="low",
            handler=self._tool_list_dir,
        ))

        # --- glob_files ---
        self.register(ToolDef(
            name="glob_files",
            description="按模式搜索文件",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式，如 *.py"},
                    "path": {"type": "string", "description": "搜索根目录", "default": "."},
                },
                "required": ["pattern"],
            },
            risk_level="low",
            handler=self._tool_glob_files,
        ))

        # --- write_file ---
        self.register(ToolDef(
            name="write_file",
            description="写入/创建文件",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
            risk_level="medium",
            handler=self._tool_write_file,
        ))

        # --- edit_file ---
        self.register(ToolDef(
            name="edit_file",
            description="精确替换文件中的内容片段",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "old_text": {"type": "string", "description": "要替换的原文"},
                    "new_text": {"type": "string", "description": "替换后的新文"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            risk_level="medium",
            handler=self._tool_edit_file,
        ))

        # --- run_command ---
        self.register(ToolDef(
            name="run_command",
            description="执行 shell 命令",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "超时秒数", "default": 60},
                },
                "required": ["command"],
            },
            risk_level="high",
            handler=self._tool_run_command,
        ))

        # --- run_python_sandbox ---
        self.register(ToolDef(
            name="run_python_sandbox",
            description="在受限沙箱中执行 Python 代码，适合计算、文本与 JSON 数据处理；禁止文件、网络、进程和动态导入",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"},
                    "timeout": {"type": "integer", "description": "超时秒数（1-15）", "default": 8},
                },
                "required": ["code"],
            },
            risk_level="low",
            handler=self._tool_run_python_sandbox,
        ))

        # --- ocr_image ---
        self.register(ToolDef(
            name="ocr_image",
            description="识别图片中的文字（当 LLM 不支持视觉时使用）",
            parameters={
                "type": "object",
                "properties": {
                    "path_or_url": {"type": "string", "description": "图片本地路径或 URL"},
                },
                "required": ["path_or_url"],
            },
            risk_level="safe",
            handler=self._tool_ocr_image,
        ))

    # === 工具实现 ===

    async def _tool_search_knowledge(self, query: str, top_k: int = 5) -> str:
        from ..knowledge.db import KnowledgeDB
        from ..knowledge.embeddings import EmbeddingClient

        results = []

        # FTS5 检索
        kb = KnowledgeDB()
        fts_results = await kb.search_fts(query, limit=top_k)
        for item in fts_results:
            results.append({
                "id": item['id'], "title": item['title'],
                "source": item['source'], "snippet": (item.get('summary_md') or '')[:200],
                "method": "keyword",
            })

        # 向量检索（如果配置了 embedding）
        try:
            emb = EmbeddingClient()
            vec_results = await emb.search_similar(query, top_k=top_k)
            for vr in vec_results:
                # 去重
                if not any(r['id'] == vr['item_id'] for r in results):
                    results.append({
                        "id": vr['item_id'], "title": vr['title'],
                        "snippet": vr['text'][:200],
                        "score": round(vr['score'], 3),
                        "method": "semantic",
                    })
        except Exception:
            pass  # 嵌入未配置时退化为仅关键词

        return json.dumps(results[:top_k], ensure_ascii=False)

    async def _tool_get_item(self, item_id: int) -> str:
        from ..knowledge.db import KnowledgeDB
        kb = KnowledgeDB()
        item = await kb.get_item(item_id)
        if not item:
            return json.dumps({"error": "条目不存在"}, ensure_ascii=False)
        return json.dumps(item, ensure_ascii=False, default=str)

    async def _tool_run_pipeline(self, url: str, template: str = "knowledge") -> str:
        from ..pipeline import get_pipeline
        pipeline = get_pipeline()
        task_id = await pipeline.submit(input_text=url, template=template)
        return json.dumps({"status": "submitted", "task_id": task_id}, ensure_ascii=False)

    async def _tool_web_search(self, query: str) -> str:
        from .web_tools import web_search
        return await web_search(query)

    async def _tool_web_fetch(self, url: str) -> str:
        from .web_tools import web_fetch
        return await web_fetch(url)

    async def _tool_read_file(self, path: str) -> str:
        p = Path(path)
        if not p.exists():
            return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
        try:
            content = p.read_text(encoding='utf-8')
            if len(content) > 10000:
                content = content[:10000] + "\n... (截断，文件过长)"
            return content
        except UnicodeDecodeError:
            return json.dumps({"error": "非文本文件，无法读取"}, ensure_ascii=False)

    async def _tool_list_dir(self, path: str) -> str:
        p = Path(path)
        if not p.exists() or not p.is_dir():
            return json.dumps({"error": f"目录不存在: {path}"}, ensure_ascii=False)
        items = []
        for item in sorted(p.iterdir()):
            items.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
            })
        return json.dumps(items[:100], ensure_ascii=False)

    async def _tool_glob_files(self, pattern: str, path: str = ".") -> str:
        p = Path(path)
        if not p.exists():
            return json.dumps({"error": f"路径不存在: {path}"}, ensure_ascii=False)
        files = [str(f) for f in p.rglob(pattern)][:50]
        return json.dumps(files, ensure_ascii=False)

    async def _tool_write_file(self, path: str, content: str) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')
        return json.dumps({"status": "ok", "path": str(p), "size": len(content)}, ensure_ascii=False)

    async def _tool_edit_file(self, path: str, old_text: str, new_text: str) -> str:
        p = Path(path)
        if not p.exists():
            return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
        content = p.read_text(encoding='utf-8')
        if old_text not in content:
            return json.dumps({"error": "未找到要替换的内容"}, ensure_ascii=False)
        new_content = content.replace(old_text, new_text, 1)
        p.write_text(new_content, encoding='utf-8')
        return json.dumps({"status": "ok", "path": str(p)}, ensure_ascii=False)

    async def _tool_run_command(self, command: str, timeout: int = 60) -> str:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(BASE_DIR),
            )
            output = result.stdout[-3000:] if result.stdout else ""
            error = result.stderr[-1000:] if result.stderr else ""
            return json.dumps({
                "returncode": result.returncode,
                "stdout": output,
                "stderr": error,
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"命令超时（{timeout}s）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def _tool_run_python_sandbox(self, code: str, timeout: int = 8) -> str:
        """在独立解释器中运行经过 AST 校验的 Python 代码。"""
        timeout = max(1, min(int(timeout), 15))
        runner = Path(__file__).with_name("sandbox_runner.py")
        env = {
            key: value for key, value in os.environ.items()
            if not any(secret in key.upper() for secret in ("KEY", "TOKEN", "PASSWORD", "SECRET"))
        }
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        try:
            with tempfile.TemporaryDirectory(prefix="lfv-sandbox-") as temp_dir:
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-X", "utf8", "-I", "-S", str(runner)],
                    input=code,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    cwd=temp_dir,
                    env=env,
                )
            return json.dumps({
                "returncode": result.returncode,
                "stdout": (result.stdout or "")[-6000:],
                "stderr": (result.stderr or "")[-2000:],
                "sandbox": "restricted-python",
            }, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"沙箱执行超时（{timeout}s）"}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def _tool_ocr_image(self, path_or_url: str) -> str:
        from ..ocr import ocr_image
        text = await ocr_image(path_or_url)
        return text
