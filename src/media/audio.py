"""音频处理模块：提取、压缩、分割

依赖 ffmpeg 命令行工具。
"""

import asyncio
import subprocess
from pathlib import Path
from typing import List, Optional

from ..config import get_config


def _run_ffmpeg(args: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """执行 ffmpeg 命令"""
    cmd = ["ffmpeg", "-y"] + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 执行失败: {result.stderr[:500]}")
    return result


def _get_duration(filepath: Path) -> float:
    """获取音频/视频时长（秒）"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(filepath)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 0.0
    import json
    try:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (json.JSONDecodeError, ValueError):
        return 0.0


async def extract_audio(
    video_path: Path,
    output_path: Optional[Path] = None,
) -> Path:
    """从视频文件提取音频（MP3）

    Args:
        video_path: 视频文件路径
        output_path: 输出路径，默认同目录 .mp3

    Returns:
        音频文件路径
    """
    if output_path is None:
        output_path = video_path.with_suffix('.mp3')

    args = [
        "-i", str(video_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "2",
        str(output_path),
    ]

    await asyncio.to_thread(_run_ffmpeg, args)
    return output_path


async def compress_audio(
    audio_path: Path,
    output_path: Optional[Path] = None,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    """压缩音频：16kHz 单声道，降低体积

    Args:
        audio_path: 输入音频路径
        output_path: 输出路径
        sample_rate: 采样率
        channels: 声道数

    Returns:
        压缩后音频路径
    """
    if output_path is None:
        output_path = audio_path.with_stem(audio_path.stem + "_compressed")

    args = [
        "-i", str(audio_path),
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-acodec", "libmp3lame",
        "-q:a", "5",
        str(output_path),
    ]

    await asyncio.to_thread(_run_ffmpeg, args)
    return output_path


async def split_audio(
    audio_path: Path,
    output_dir: Optional[Path] = None,
    segment_duration_sec: Optional[int] = None,
) -> List[Path]:
    """长音频分割

    超过阈值则按 segment_duration_sec 分片。
    若未超阈值则返回原文件列表。

    Args:
        audio_path: 音频文件
        output_dir: 分片输出目录
        segment_duration_sec: 每段时长（秒）

    Returns:
        分片文件路径列表
    """
    config = get_config()
    if segment_duration_sec is None:
        segment_duration_sec = config.pipeline.segment_duration_sec

    # 检查是否需要分割
    duration = await asyncio.to_thread(_get_duration, audio_path)
    file_size_mb = audio_path.stat().st_size / (1024 * 1024)

    needs_split = (
        duration > config.pipeline.split_duration_min * 60
        or file_size_mb > config.pipeline.split_size_mb
    )

    if not needs_split:
        return [audio_path]

    # 执行分割
    if output_dir is None:
        output_dir = audio_path.parent / "segments"
    output_dir.mkdir(parents=True, exist_ok=True)

    segments = []
    current_time = 0.0
    index = 0

    while current_time < duration:
        segment_path = output_dir / f"segment_{index:03d}.mp3"
        args = [
            "-i", str(audio_path),
            "-ss", str(current_time),
            "-t", str(segment_duration_sec),
            "-acodec", "libmp3lame",
            "-q:a", "2",
            str(segment_path),
        ]
        await asyncio.to_thread(_run_ffmpeg, args)
        segments.append(segment_path)
        current_time += segment_duration_sec
        index += 1

    return segments


async def get_audio_duration(filepath: Path) -> float:
    """获取音频时长（秒）"""
    return await asyncio.to_thread(_get_duration, filepath)
