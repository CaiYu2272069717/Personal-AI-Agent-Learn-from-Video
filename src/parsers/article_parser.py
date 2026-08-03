"""文章 URL 解析器：正文抓取转 Markdown

支持公众号、知乎、普通网页。
"""

import asyncio
from typing import Optional

from .router import ParseResult


class ArticleParser:
    """网页文章解析器"""

    async def parse(self, url: str) -> ParseResult:
        """抓取文章正文"""
        return await asyncio.to_thread(self._parse_sync, url)

    def _parse_sync(self, url: str) -> ParseResult:
        """同步解析"""
        import trafilatura

        # 下载页面
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise ValueError(f"无法获取网页内容: {url}")

        # 提取正文 (Markdown 格式)
        text = trafilatura.extract(
            downloaded,
            output_format='markdown',
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )

        if not text:
            raise ValueError(f"无法提取正文内容: {url}")

        # 提取元数据
        metadata = trafilatura.extract_metadata(downloaded)
        title = ""
        author = ""
        if metadata:
            title = metadata.title or ""
            author = metadata.author or ""

        if not title:
            # 尝试从 URL 推断
            title = url.split('/')[-1].split('?')[0] or "untitled"

        return ParseResult(
            source='article',
            source_id=url,  # 文章用 URL 作为 source_id
            title=title,
            url=url,
            author=author,
            content_type='article',
            text_content=text,
        )
