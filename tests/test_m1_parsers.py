"""M1 链接解析模块测试"""

import pytest
from unittest.mock import patch, MagicMock


def test_extract_url():
    """测试 URL 提取"""
    from src.parsers.router import extract_url

    # 标准 URL
    assert extract_url("看看这个 https://www.bilibili.com/video/BV123 很棒") == "https://www.bilibili.com/video/BV123"
    # 抖音分享
    assert extract_url("5.88 04/22 Hyr:/ 复制打开抖音 https://v.douyin.com/abc123/") == "https://v.douyin.com/abc123/"
    # 无链接
    assert extract_url("没有链接") is None
    # 多个链接取第一个
    assert extract_url("https://a.com https://b.com") == "https://a.com"


def test_detect_source_type():
    """测试来源类型检测"""
    from src.parsers.router import detect_source_type

    assert detect_source_type("https://v.douyin.com/abc/") == "douyin"
    assert detect_source_type("https://www.iesdouyin.com/share/video/123") == "douyin"
    assert detect_source_type("https://www.bilibili.com/video/BV1xx") == "bilibili"
    assert detect_source_type("https://b23.tv/abc123") == "bilibili"
    assert detect_source_type("https://mp.weixin.qq.com/s/xxx") == "article"
    assert detect_source_type("https://zhuanlan.zhihu.com/p/123") == "article"


def test_parse_result_dataclass():
    """测试 ParseResult 数据结构"""
    from src.parsers.router import ParseResult

    r = ParseResult(source="douyin", source_id="123", title="test")
    assert r.source == "douyin"
    assert r.content_type == "video"  # 默认
    assert r.is_multi_part is False
    assert r.parts == []
    assert r.image_urls == []


@pytest.mark.asyncio
async def test_douyin_parser_video():
    """测试抖音视频解析（mock HTTP）"""
    from src.parsers.douyin_parser import DouyinParser

    # Mock requests.get
    mock_redirect_resp = MagicMock()
    mock_redirect_resp.url = "https://www.douyin.com/video/7300000000000000000?xxx"

    mock_page_resp = MagicMock()
    mock_page_resp.status_code = 200
    mock_page_resp.raise_for_status = MagicMock()
    router_data = {
        "loaderData": {
            "video_(id)/page": {
                "videoInfoRes": {
                    "item_list": [{
                        "desc": "测试视频标题",
                        "author": {"nickname": "测试作者"},
                        "video": {
                            "play_addr": {
                                "url_list": ["https://cdn.douyin.com/playwm/abc"]
                            },
                            "duration": 30000
                        }
                    }]
                }
            }
        }
    }
    import json
    mock_page_resp.text = f'<script>window._ROUTER_DATA = {json.dumps(router_data)}</script>'

    with patch('src.parsers.douyin_parser.requests.get') as mock_get:
        mock_get.side_effect = [mock_redirect_resp, mock_page_resp]
        parser = DouyinParser()
        result = await parser.parse("https://v.douyin.com/abc123/")

    assert result.source == "douyin"
    assert result.source_id == "7300000000000000000"
    assert result.title == "测试视频标题"
    assert result.content_type == "video"
    assert "play" in result.video_url  # playwm -> play
    assert "playwm" not in result.video_url
    assert result.author == "测试作者"
    assert result.duration_sec == 30


@pytest.mark.asyncio
async def test_douyin_parser_note():
    """测试抖音图集解析（mock HTTP）"""
    from src.parsers.douyin_parser import DouyinParser
    import json

    mock_redirect_resp = MagicMock()
    mock_redirect_resp.url = "https://www.douyin.com/note/7400000000000000000?xxx"

    router_data = {
        "loaderData": {
            "note_(id)/page": {
                "videoInfoRes": {
                    "item_list": [{
                        "desc": "图集测试",
                        "author": {"nickname": "作者A"},
                        "images": [
                            {"url_list": ["https://img1.com/a.jpg"]},
                            {"url_list": ["https://img2.com/b.jpg"]},
                        ]
                    }]
                }
            }
        }
    }
    mock_page_resp = MagicMock()
    mock_page_resp.status_code = 200
    mock_page_resp.raise_for_status = MagicMock()
    mock_page_resp.text = f'<script>window._ROUTER_DATA = {json.dumps(router_data)}</script>'

    with patch('src.parsers.douyin_parser.requests.get') as mock_get:
        mock_get.side_effect = [mock_redirect_resp, mock_page_resp]
        parser = DouyinParser()
        result = await parser.parse("https://v.douyin.com/note123/")

    assert result.content_type == "note"
    assert len(result.image_urls) == 2
    assert result.title == "图集测试"


@pytest.mark.asyncio
async def test_bilibili_parser():
    """测试 B 站解析（mock yt-dlp）"""
    from src.parsers.bilibili_parser import BilibiliParser

    mock_info = {
        'id': 'BV1test',
        'title': 'B站测试视频',
        'webpage_url': 'https://www.bilibili.com/video/BV1test',
        'url': 'https://cdn.bilibili.com/video.mp4',
        'uploader': 'UP主',
        'duration': 300,
    }

    with patch('yt_dlp.YoutubeDL') as mock_ydl_class:
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value = mock_ydl

        parser = BilibiliParser()
        result = await parser.parse("https://www.bilibili.com/video/BV1test")

    assert result.source == "bilibili"
    assert result.title == "B站测试视频"
    assert result.author == "UP主"
    assert result.duration_sec == 300
    assert result.is_multi_part is False


@pytest.mark.asyncio
async def test_bilibili_parser_multi_part():
    """测试 B 站多P解析"""
    from src.parsers.bilibili_parser import BilibiliParser

    mock_info = {
        'id': 'BV1multi',
        'title': '系列教程',
        'webpage_url': 'https://www.bilibili.com/video/BV1multi',
        'uploader': 'UP主2',
        'entries': [
            {'title': 'P1 介绍', 'url': 'url1', 'duration': 120, 'id': 'p1'},
            {'title': 'P2 进阶', 'url': 'url2', 'duration': 180, 'id': 'p2'},
        ],
    }

    with patch('yt_dlp.YoutubeDL') as mock_ydl_class:
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = mock_info
        mock_ydl_class.return_value = mock_ydl

        parser = BilibiliParser()
        result = await parser.parse("https://www.bilibili.com/video/BV1multi")

    assert result.is_multi_part is True
    assert len(result.parts) == 2
    assert result.parts[0]['title'] == 'P1 介绍'


@pytest.mark.asyncio
async def test_article_parser():
    """测试文章解析（mock trafilatura）"""
    from src.parsers.article_parser import ArticleParser

    mock_metadata = MagicMock()
    mock_metadata.title = "测试文章标题"
    mock_metadata.author = "文章作者"

    with patch('trafilatura.fetch_url', return_value="<html><body><p>Hello World</p></body></html>"):
        with patch('trafilatura.extract', return_value="# Hello\n\nWorld content here"):
            with patch('trafilatura.extract_metadata', return_value=mock_metadata):
                parser = ArticleParser()
                result = await parser.parse("https://mp.weixin.qq.com/s/xxx")

    assert result.source == "article"
    assert result.title == "测试文章标题"
    assert result.content_type == "article"
    assert "Hello" in result.text_content


@pytest.mark.asyncio
async def test_parse_url_routing():
    """测试路由自动分发"""
    from src.parsers.router import parse_url

    # 无有效链接
    with pytest.raises(ValueError, match="未找到有效的链接"):
        await parse_url("没有链接的文本")
