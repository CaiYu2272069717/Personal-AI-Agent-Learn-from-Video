"""LLM 总结模块：结构化总结 + 模板 + 超长文本 map-reduce

独立 LLM 配置，与 ASR 分离。
"""

import asyncio
from pathlib import Path
from typing import Optional, List

from openai import AsyncOpenAI

from .config import get_config, PROMPTS_DIR


DEFAULT_TEMPLATE = "knowledge"  # 默认模板

# 大约 token 长度估算（中文约 1.5 字/token）
MAX_CHUNK_CHARS = 6000  # 单次总结最大字符


class SummaryClient:
    """LLM 总结客户端"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        config = get_config().llm
        self.base_url = base_url or config.base_url
        self.api_key = api_key or config.api_key
        self.model = model or config.model
        self.temperature = temperature if temperature is not None else config.temperature

        if not self.api_key:
            raise ValueError("LLM API Key 未配置")

        self.client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

    def _load_template(self, template_name: str) -> str:
        """加载 Prompt 模板"""
        template_path = PROMPTS_DIR / f"{template_name}.md"
        if template_path.exists():
            return template_path.read_text(encoding='utf-8')
        # fallback 到默认内置模板
        return self._builtin_template(template_name)

    def _builtin_template(self, name: str) -> str:
        """内置模板"""
        templates = {
            "knowledge": """你是一个专业的学习笔记整理助手。请将以下转录文本整理成结构化的学习笔记。

要求输出格式（Markdown）：
## 一句话总结
（不超过 50 字概括核心内容）

## 核心要点
- （3-7 条核心观点，每条 1-2 句话）

## 详细笔记
（按逻辑分段整理详细内容，保留重要信息和论证）

## 金句摘录
> （原文中有价值的精彩表达，2-5 条）

## 行动清单
- [ ] （可执行的具体行动项，如有）

注意：
- 修正明显的语音识别错误
- 保持原意，不添加主观评价
- 专有名词保持准确""",

            "tutorial": """你是一个教程整理助手。请将以下教程转录文本整理成步骤清晰的教程笔记。

输出格式：
## 教程概要
## 前置条件
## 步骤详解
### 步骤 1: ...
### 步骤 2: ...
## 常见问题
## 总结""",

            "opinion": """你是一个观点整理助手。请提炼以下内容中的核心观点和论证。

输出格式：
## 核心观点
## 论证过程
## 反方观点（如有）
## 我的思考空间""",

            "quick": """请用 3-5 句话总结以下内容的核心要点，简洁明了。""",
        }
        return templates.get(name, templates["knowledge"])

    async def summarize(
        self,
        transcript: str,
        title: str = "",
        template: str = DEFAULT_TEMPLATE,
    ) -> str:
        """生成结构化总结

        自动处理超长文本（map-reduce）。

        Args:
            transcript: 转录文本
            title: 视频/文章标题
            template: 模板名称

        Returns:
            Markdown 格式的总结
        """
        system_prompt = self._load_template(template)

        # 判断是否需要 map-reduce
        if len(transcript) <= MAX_CHUNK_CHARS:
            return await self._single_summarize(system_prompt, transcript, title)
        else:
            return await self._map_reduce_summarize(system_prompt, transcript, title)

    async def _single_summarize(
        self,
        system_prompt: str,
        text: str,
        title: str,
    ) -> str:
        """单次总结"""
        user_content = f"标题：{title}\n\n转录内容：\n{text}" if title else text

        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content or ""

    async def _map_reduce_summarize(
        self,
        system_prompt: str,
        text: str,
        title: str,
    ) -> str:
        """Map-Reduce：分块总结后合并"""
        # Map: 分块摘要
        chunks = self._split_text(text, MAX_CHUNK_CHARS)
        chunk_summaries = []

        for i, chunk in enumerate(chunks):
            prompt = f"这是第 {i+1}/{len(chunks)} 部分。请提取核心要点（简要列表形式）：\n\n{chunk}"
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": "你是内容摘要助手，请提炼要点。"},
                    {"role": "user", "content": prompt},
                ],
            )
            chunk_summaries.append(response.choices[0].message.content or "")

        # Reduce: 合并为最终总结
        merged = "\n\n".join([
            f"### 第 {i+1} 部分要点\n{s}"
            for i, s in enumerate(chunk_summaries)
        ])

        user_content = f"标题：{title}\n\n以下是分段要点汇总，请整合为完整的结构化笔记：\n\n{merged}"

        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        return response.choices[0].message.content or ""

    async def generate_tags(self, summary: str) -> List[str]:
        """自动生成分类标签"""
        response = await self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": "根据内容生成 3-5 个分类标签，仅返回逗号分隔的标签，不要其他文字。"},
                {"role": "user", "content": summary[:2000]},
            ],
        )
        tags_text = response.choices[0].message.content or ""
        return [t.strip() for t in tags_text.split(',') if t.strip()]

    @staticmethod
    def _split_text(text: str, max_chars: int) -> List[str]:
        """按字符数分割文本（尽量在句号处断开）"""
        chunks = []
        while len(text) > max_chars:
            # 在 max_chars 附近找最后一个句号
            split_point = text.rfind('。', 0, max_chars)
            if split_point == -1:
                split_point = text.rfind('.', 0, max_chars)
            if split_point == -1 or split_point < max_chars // 2:
                split_point = max_chars
            else:
                split_point += 1  # 包含句号

            chunks.append(text[:split_point])
            text = text[split_point:]

        if text:
            chunks.append(text)
        return chunks
