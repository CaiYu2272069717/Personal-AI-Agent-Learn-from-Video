"""Agent 工具注册表

所有 Agent 可调用的工具定义与实现。
"""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass

from ..config import get_config, WORKDIR, BASE_DIR


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

    async def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """执行工具"""
        tool = self._tools.get(name)
        if not tool:
            return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        if not tool.handler:
            return json.dumps({"error": f"工具 {name} 无处理函数"}, ensure_ascii=False)

        try:
            result = await tool.handler(**arguments)
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
            description="联网搜索，返回结果列表（标题+URL+摘要）",
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

    async def _tool_ocr_image(self, path_or_url: str) -> str:
        from ..ocr import ocr_image
        text = await ocr_image(path_or_url)
        return text
