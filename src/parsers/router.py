"""URL 自动路由：根据域名分发到对应解析器"""

import re
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class ParseResult:
    """解析结果统一格式"""
    source: str  # douyin / bilibili / article
    source_id: str = ""
    title: str = ""
    url: str = ""
    video_url: str = ""  # 无水印直链（视频类）
    audio_url: str = ""  # 音频直链（B站音频流）
    author: str = ""
    duration_sec: int = 0
    content_type: str = "video"  # video / note(图集) / article
    is_multi_part: bool = False
    parts: list = field(default_factory=list)  # 多P列表
    text_content: str = ""  # 文章正文
    image_urls: list = field(default_factory=list)  # 图集URL列表


def extract_url(text: str) -> Optional[str]:
    """从文本中提取第一个 URL"""
    urls = re.findall(
        r'https?://[^\s<>"\']+'  ,
        text
    )
    return urls[0].rstrip('.,;!?') if urls else None


def detect_source_type(url: str) -> str:
    """根据 URL 判断来源类型"""
    url_lower = url.lower()
    if any(d in url_lower for d in ['douyin.com', 'iesdouyin.com']):
        return 'douyin'
    if any(d in url_lower for d in ['bilibili.com', 'b23.tv']):
        return 'bilibili'
    return 'article'


async def parse_url(text: str) -> ParseResult:
    """主入口：解析链接文本，返回统一结果"""
    url = extract_url(text)
    if not url:
        raise ValueError("未找到有效的链接")

    source_type = detect_source_type(url)

    if source_type == 'douyin':
        from .douyin_parser import DouyinParser
        parser = DouyinParser()
        return await parser.parse(text)
    elif source_type == 'bilibili':
        from .bilibili_parser import BilibiliParser
        parser = BilibiliParser()
        return await parser.parse(url)
    else:
        from .article_parser import ArticleParser
        parser = ArticleParser()
        return await parser.parse(url)
