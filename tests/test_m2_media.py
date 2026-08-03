"""M2 媒体下载与音频处理测试"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_download_video(tmp_path):
    """测试视频下载（mock HTTP）"""
    from src.media.downloader import download_video

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {'content-length': '1024'}
    mock_response.iter_content.return_value = [b'x' * 512, b'y' * 512]

    progress_calls = []

    def on_progress(downloaded, total):
        progress_calls.append((downloaded, total))

    with patch('src.media.downloader.requests.get', return_value=mock_response):
        result = await download_video(
            "https://example.com/video.mp4",
            tmp_path,
            "test.mp4",
            progress_callback=on_progress,
        )

    assert result == tmp_path / "test.mp4"
    assert result.exists()
    assert result.stat().st_size == 1024
    assert len(progress_calls) == 2


@pytest.mark.asyncio
async def test_download_video_retry(tmp_path):
    """测试下载重试"""
    from src.media.downloader import download_video

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("连接失败")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {'content-length': '10'}
        mock_resp.iter_content.return_value = [b'0123456789']
        return mock_resp

    with patch('src.media.downloader.requests.get', side_effect=side_effect):
        result = await download_video(
            "https://example.com/video.mp4",
            tmp_path,
            "retry.mp4",
            max_retries=3,
        )

    assert result.exists()
    assert call_count == 3


@pytest.mark.asyncio
async def test_extract_audio(tmp_path):
    """测试音频提取（mock ffmpeg）"""
    from src.media.audio import extract_audio

    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b'fake video data')
    expected_output = tmp_path / "video.mp3"

    with patch('src.media.audio._run_ffmpeg') as mock_ff:
        # 模拟 ffmpeg 创建输出文件
        def fake_ffmpeg(args, timeout=300):
            Path(args[-1]).write_bytes(b'fake audio')
            return MagicMock(returncode=0)
        mock_ff.side_effect = fake_ffmpeg

        result = await extract_audio(video_file)

    assert result == expected_output
    assert result.exists()


@pytest.mark.asyncio
async def test_compress_audio(tmp_path):
    """测试音频压缩"""
    from src.media.audio import compress_audio

    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b'fake audio data')

    with patch('src.media.audio._run_ffmpeg') as mock_ff:
        def fake_ffmpeg(args, timeout=300):
            Path(args[-1]).write_bytes(b'compressed')
            return MagicMock(returncode=0)
        mock_ff.side_effect = fake_ffmpeg

        result = await compress_audio(audio_file)

    assert "compressed" in result.stem
    assert result.exists()


@pytest.mark.asyncio
async def test_split_audio_no_split_needed(tmp_path, monkeypatch):
    """测试：短音频不需要分割"""
    from src.media import audio as audio_mod

    audio_file = tmp_path / "short.mp3"
    audio_file.write_bytes(b'x' * 1000)  # 很小的文件

    # Mock _get_duration 返回 60 秒（小于阈值）
    with patch.object(audio_mod, '_get_duration', return_value=60.0):
        result = await audio_mod.split_audio(audio_file)

    assert result == [audio_file]


@pytest.mark.asyncio
async def test_split_audio_splits_correctly(tmp_path, monkeypatch):
    """测试：长音频正确分割"""
    from src.media import audio as audio_mod
    from src.config import get_config

    audio_file = tmp_path / "long.mp3"
    audio_file.write_bytes(b'x' * (50 * 1024 * 1024))  # 50MB

    segment_count = 0

    def fake_ffmpeg(args, timeout=300):
        nonlocal segment_count
        Path(args[-1]).write_bytes(b'segment')
        segment_count += 1
        return MagicMock(returncode=0)

    # 20 分钟音频，9 分钟/段 = 3 段
    with patch.object(audio_mod, '_get_duration', return_value=1200.0):
        with patch.object(audio_mod, '_run_ffmpeg', side_effect=fake_ffmpeg):
            result = await audio_mod.split_audio(audio_file, output_dir=tmp_path / "segs")

    # 1200 / 540 ≈ 3 段
    assert len(result) == 3
    assert all(p.exists() for p in result)
