"""B站链接解析器：基于 yt-dlp

支持 BV号、b23.tv 短链、完整 URL；多P检测。
"""

import re
import asyncio
import json
from typing import Optional

from .router import ParseResult


class BilibiliParser:
    """B站视频解析器"""

    async def parse(self, url: str) -> ParseResult:
        """解析 B 站链接"""
        return await asyncio.to_thread(self._parse_sync, url)

    def _parse_sync(self, url: str) -> ParseResult:
        """同步解析"""
        import yt_dlp

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': 'in_playlist',  # 不下载，仅提取信息
            'skip_download': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info is None:
            raise ValueError(f"无法解析 B 站链接: {url}")

        # 检测是否为合集/多P
        entries = info.get('entries')
        if entries:
            # 多P / 合集
            parts = []
            for entry in entries:
                parts.append({
                    'title': entry.get('title', ''),
                    'url': entry.get('url', '') or entry.get('webpage_url', ''),
                    'duration': entry.get('duration', 0),
                    'id': entry.get('id', ''),
                })
            return ParseResult(
                source='bilibili',
                source_id=info.get('id', ''),
                title=info.get('title', ''),
                url=info.get('webpage_url', url),
                author=info.get('uploader', ''),
                content_type='video',
                is_multi_part=True,
                parts=parts,
            )
        else:
            # 单个视频
            return ParseResult(
                source='bilibili',
                source_id=info.get('id', ''),
                title=info.get('title', ''),
                url=info.get('webpage_url', url),
                video_url=info.get('url', ''),  # 可能需要通过 format 获取
                author=info.get('uploader', ''),
                duration_sec=info.get('duration', 0) or 0,
                content_type='video',
                is_multi_part=False,
            )
