"""媒体下载模块：视频下载 + B站音频直下

支持进度回调和重试。
"""

import asyncio
import shutil
from pathlib import Path
from typing import Callable, Optional

import requests

from ..config import WORKDIR

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) '
        'EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1'
    ),
    'Referer': 'https://www.douyin.com/',
}


async def download_video(
    url: str,
    output_dir: Path,
    filename: str = "video.mp4",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    max_retries: int = 3,
) -> Path:
    """下载视频文件

    Args:
        url: 视频直链
        output_dir: 输出目录
        filename: 文件名
        progress_callback: 进度回调 (downloaded_bytes, total_bytes)
        max_retries: 最大重试次数

    Returns:
        下载文件路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename

    for attempt in range(max_retries):
        try:
            result = await asyncio.to_thread(
                _download_sync, url, filepath, progress_callback
            )
            return result
        except Exception as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"下载失败（重试 {max_retries} 次）: {e}")
            await asyncio.sleep(1 * (attempt + 1))

    return filepath  # unreachable


def _download_sync(
    url: str,
    filepath: Path,
    progress_callback: Optional[Callable] = None,
) -> Path:
    """同步下载实现"""
    response = requests.get(url, headers=HEADERS, stream=True, timeout=60)
    response.raise_for_status()

    total = int(response.headers.get('content-length', 0))
    downloaded = 0

    with open(filepath, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)

    return filepath


async def download_audio_only(
    url: str,
    output_dir: Path,
    filename: str = "audio.mp3",
) -> Path:
    """B站音频直下（通过 yt-dlp）"""
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / filename

    def _do():
        import yt_dlp
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(filepath.with_suffix('.%(ext)s')),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        # yt-dlp 会加后缀，找到实际文件
        actual = filepath.with_suffix('.mp3')
        if actual.exists():
            return actual
        # fallback
        for f in output_dir.glob("audio*"):
            return f
        raise FileNotFoundError(f"音频下载后未找到文件: {output_dir}")

    return await asyncio.to_thread(_do)
