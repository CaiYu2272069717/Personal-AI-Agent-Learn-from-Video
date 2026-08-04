"""抖音链接解析器：支持 video 与 note（图集）

移植自 temp/douyin-mcp-server，改造为异步、独立类。
"""

import re
import json
import asyncio
from typing import Optional

import requests

from .router import ParseResult

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) '
        'EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1'
    )
}


class DouyinParser:
    """抖音视频/图集解析器"""

    async def parse(self, share_text: str) -> ParseResult:
        """从分享文本解析无水印链接"""
        return await asyncio.to_thread(self._parse_sync, share_text)

    def _parse_sync(self, share_text: str) -> ParseResult:
        """同步解析逻辑"""
        # 提取 URL
        urls = re.findall(
            r'https?://[^\s<>"\']+'  ,
            share_text
        )
        if not urls:
            raise ValueError("未找到有效的抖音分享链接")

        share_url = urls[0].rstrip('.,;!?')

        # 跟随重定向获取 video_id
        response = requests.get(share_url, headers=HEADERS, allow_redirects=True, timeout=10)
        video_id = response.url.split("?")[0].strip("/").split("/")[-1]

        # 构造标准 URL
        canonical_url = f'https://www.iesdouyin.com/share/video/{video_id}'

        # 获取页面内容
        page_resp = requests.get(canonical_url, headers=HEADERS, timeout=10)
        page_resp.raise_for_status()

        # 解析 _ROUTER_DATA
        pattern = re.compile(
            r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        match = pattern.search(page_resp.text)
        if not match or not match.group(1):
            raise ValueError("抖音页面解析失败：未找到 _ROUTER_DATA")

        json_data = json.loads(match.group(1).strip())
        loader_data = json_data.get("loaderData", {})

        VIDEO_KEY = "video_(id)/page"
        NOTE_KEY = "note_(id)/page"

        if VIDEO_KEY in loader_data:
            info_res = loader_data[VIDEO_KEY]["videoInfoRes"]
            content_type = "video"
        elif NOTE_KEY in loader_data:
            info_res = loader_data[NOTE_KEY]["videoInfoRes"]
            content_type = "note"
        else:
            raise ValueError("无法从抖音页面解析视频或图集信息")

        item_data = info_res["item_list"][0]

        # 提取标题
        title = item_data.get("desc", "").strip() or f"douyin_{video_id}"
        title = re.sub(r'[\\/:*?"<>|]', '_', title)

        # 提取作者
        author = item_data.get("author", {}).get("nickname", "")

        # 视频或图集
        if content_type == "video":
            video_url = item_data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
            duration = item_data.get("video", {}).get("duration", 0) // 1000  # ms -> s
            return ParseResult(
                source="douyin",
                source_id=video_id,
                title=title,
                url=canonical_url,
                video_url=video_url,
                author=author,
                duration_sec=duration,
                content_type="video",
            )
        else:
            # 图集：提取图片 URL 列表
            images = item_data.get("images", []) or []
            image_urls = []
            for img in images:
                url_list = img.get("url_list", [])
                if url_list:
                    image_urls.append(url_list[0])
            return ParseResult(
                source="douyin",
                source_id=video_id,
                title=title,
                url=canonical_url,
                author=author,
                content_type="note",
                image_urls=image_urls,
            )
