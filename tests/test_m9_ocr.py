"""M9 OCR 模块测试"""

import pytest
import json
import httpx
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


@pytest.mark.asyncio
async def test_ocr_vlm_with_url():
    """测试 VLM OCR（URL 输入）"""
    from src.ocr import ocr_image

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "识别到的文字内容"}}]
    }

    with patch('src.ocr.get_config') as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ocr=MagicMock(
                backend="vlm",
                vlm_base_url="http://test/v1",
                vlm_api_key="test-key",
                vlm_model="test-model",
            )
        )
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await ocr_image("https://example.com/image.jpg")

    assert "识别到的文字内容" in result


@pytest.mark.asyncio
async def test_ocr_vlm_with_local_file(tmp_path):
    """测试 VLM OCR（本地文件输入）"""
    from src.ocr import ocr_image

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b'\x89PNG fake data')

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "本地图片文字"}}]
    }

    with patch('src.ocr.get_config') as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ocr=MagicMock(
                backend="vlm",
                vlm_base_url="http://test/v1",
                vlm_api_key="test-key",
                vlm_model="test-model",
            )
        )
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await ocr_image(str(img_file))

    assert "本地图片文字" in result


@pytest.mark.asyncio
async def test_ocr_no_api_key():
    """测试无 API Key"""
    from src.ocr import ocr_image

    with patch('src.ocr.get_config') as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ocr=MagicMock(
                backend="vlm",
                vlm_base_url="http://test/v1",
                vlm_api_key="",
                vlm_model="test-model",
            )
        )
        result = await ocr_image("https://example.com/img.jpg")

    data = json.loads(result)
    assert "error" in data
    assert "未配置" in data["error"]


@pytest.mark.asyncio
async def test_ocr_paddleocr_fallback():
    """测试 PaddleOCR 后端（未实现）"""
    from src.ocr import ocr_image

    with patch('src.ocr.get_config') as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ocr=MagicMock(backend="paddleocr")
        )
        result = await ocr_image("test.jpg")

    data = json.loads(result)
    assert "error" in data
    assert "尚未实现" in data["error"]


@pytest.mark.asyncio
async def test_ocr_images_batch():
    """测试批量 OCR"""
    from src.ocr import ocr_images_batch

    with patch('src.ocr.ocr_image', new_callable=AsyncMock) as mock_ocr:
        mock_ocr.side_effect = ["文字1", "文字2", "文字3"]
        result = await ocr_images_batch(["img1.jpg", "img2.jpg", "img3.jpg"])

    assert "图片 1" in result
    assert "文字1" in result
    assert "文字3" in result


@pytest.mark.asyncio
async def test_ocr_timeout_has_diagnostic_message():
    """空字符串的超时异常也应返回可诊断错误。"""
    from src.ocr import ocr_image

    with patch('src.ocr.get_config') as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ocr=MagicMock(
                backend="vlm",
                vlm_base_url="http://test/v1",
                vlm_api_key="test-key",
                vlm_model="test-model",
            )
        )
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout(""))
            mock_client_class.return_value = mock_client

            result = await ocr_image("https://example.com/image.jpg")

    data = json.loads(result)
    assert "OCR 请求超时" in data["error"]
    assert "ReadTimeout" in data["error"]
    assert data["error"].strip()


@pytest.mark.asyncio
async def test_ocr_http_error_includes_server_detail():
    """接口报错时应显示服务端返回的具体原因。"""
    from src.ocr import ocr_image

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.is_success = False
    mock_response.json.return_value = {"error": {"message": "model does not support image input"}}

    with patch('src.ocr.get_config') as mock_cfg:
        mock_cfg.return_value = MagicMock(
            ocr=MagicMock(
                backend="vlm",
                vlm_base_url="http://test/v1",
                vlm_api_key="test-key",
                vlm_model="test-model",
            )
        )
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await ocr_image("https://example.com/image.jpg")

    data = json.loads(result)
    assert "400" in data["error"]
    assert "model does not support image input" in data["error"]
