"""M3 ASR 转录模块测试"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import json


@pytest.mark.asyncio
async def test_asr_transcribe_file(tmp_path, monkeypatch):
    """测试单文件转录"""
    from src.asr import ASRClient
    import src.config as cfg

    # 设置 API key
    monkeypatch.setattr(cfg, '_config', None)
    original = cfg.CONFIG_FILE
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"asr": {"api_key": "test-key"}}))
    monkeypatch.setattr(cfg, 'CONFIG_FILE', config_file)

    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b'fake audio')

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "这是转录结果"}

    with patch('httpx.AsyncClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        client = ASRClient(api_key="test-key")
        result = await client.transcribe_file(audio_file, context="测试上下文")

    assert result == "这是转录结果"
    monkeypatch.setattr(cfg, 'CONFIG_FILE', original)


@pytest.mark.asyncio
async def test_asr_transcribe_segments(tmp_path, monkeypatch):
    """测试分段并发转录"""
    from src.asr import ASRClient
    import src.config as cfg

    monkeypatch.setattr(cfg, '_config', None)
    original = cfg.CONFIG_FILE
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"asr": {"api_key": "test-key", "concurrent": 2}}))
    monkeypatch.setattr(cfg, 'CONFIG_FILE', config_file)

    # 创建 3 个模拟音频文件
    segments = []
    for i in range(3):
        p = tmp_path / f"seg_{i}.mp3"
        p.write_bytes(b'audio data')
        segments.append(p)

    call_count = 0

    async def mock_transcribe(self, path, context=None, max_retries=3):
        nonlocal call_count
        call_count += 1
        return f"段落{path.stem[-1]}的内容"

    with patch.object(ASRClient, 'transcribe_file', mock_transcribe):
        client = ASRClient(api_key="test-key")
        progress = []
        result = await client.transcribe_segments(
            segments,
            context="test",
            progress_callback=lambda c, t: progress.append((c, t)),
        )

    assert "段落0的内容" in result
    assert "段落1的内容" in result
    assert "段落2的内容" in result
    assert len(progress) == 3
    assert call_count == 3
    monkeypatch.setattr(cfg, 'CONFIG_FILE', original)


def test_asr_no_api_key():
    """测试无 API Key 报错"""
    from src.asr import ASRClient
    with pytest.raises(ValueError, match="API Key 未配置"):
        ASRClient(api_key="")
