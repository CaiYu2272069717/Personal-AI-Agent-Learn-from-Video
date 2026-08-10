"""OCR 图文识别模块

支持两种后端：
1. 视觉 LLM API（默认）
2. PaddleOCR 本地（P2，待实现）
"""

import json
import base64
from pathlib import Path
from typing import Optional

import httpx

from .config import get_config


async def ocr_image(path_or_url: str) -> str:
    """识别图片中的文字

    Args:
        path_or_url: 本地路径或图片 URL

    Returns:
        识别到的文字内容
    """
    config = get_config().ocr

    if config.backend == "vlm":
        return await _ocr_vlm(path_or_url, config)
    else:
        return await _ocr_paddleocr(path_or_url)


def _format_exception(exc: Exception) -> str:
    """为异常生成始终非空、可诊断的说明。"""
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _response_error_detail(response: httpx.Response) -> str:
    """尽量从 OpenAI 兼容接口响应中提取错误原因。"""
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(error, str) and error.strip():
            return error.strip()
        for key in ("message", "detail"):
            if payload.get(key):
                return str(payload[key])

    body = response.text.strip()
    return body[:500] if body else "服务端未返回错误详情"


def _extract_text(result: dict) -> str:
    """兼容字符串或内容块形式的 OpenAI 消息正文。"""
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("VLM 响应缺少 choices")

    message = choices[0].get("message", {})
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("text")
        ]
        text = "\n".join(parts).strip()
        if text:
            return text
    raise ValueError("VLM 响应中没有可用的文字内容")


async def _ocr_vlm(path_or_url: str, config) -> str:
    """使用视觉 LLM 进行 OCR"""
    if not config.vlm_api_key:
        return json.dumps({"error": "OCR VLM API Key 未配置"}, ensure_ascii=False)

    # 构建图片内容
    if path_or_url.startswith(('http://', 'https://')):
        image_content = {"type": "image_url", "image_url": {"url": path_or_url}}
    else:
        # 本地文件转 base64
        path = Path(path_or_url)
        if not path.exists():
            return json.dumps({"error": f"图片不存在: {path_or_url}"}, ensure_ascii=False)
        data = base64.b64encode(path.read_bytes()).decode()
        suffix = path.suffix.lstrip('.').lower()
        mime = f"image/{suffix}" if suffix in ('png', 'gif', 'webp') else "image/jpeg"
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{data}"}
        }

    endpoint = f"{config.vlm_base_url.rstrip('/')}/chat/completions"

    payload = {
        "model": config.vlm_model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "请识别并提取图片中的所有文字内容，保持原始排版格式。只输出文字内容，不要其他说明。"},
                image_content,
            ]
        }],
        "max_tokens": 2000,
    }

    try:
        timeout = httpx.Timeout(90.0, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {config.vlm_api_key}"},
            )

        if not response.is_success:
            detail = _response_error_detail(response)
            return json.dumps(
                {"error": f"VLM OCR 请求失败 ({response.status_code}): {detail}"},
                ensure_ascii=False,
            )

        result = response.json()
        return _extract_text(result)

    except httpx.TimeoutException as exc:
        return json.dumps(
            {"error": f"OCR 请求超时（视觉模型 90 秒内未响应）: {_format_exception(exc)}"},
            ensure_ascii=False,
        )
    except httpx.RequestError as exc:
        return json.dumps(
            {"error": f"OCR 网络请求失败: {_format_exception(exc)}"},
            ensure_ascii=False,
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return json.dumps(
            {"error": f"OCR 响应解析失败: {_format_exception(exc)}"},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {"error": f"OCR 异常: {_format_exception(exc)}"},
            ensure_ascii=False,
        )


async def _ocr_paddleocr(path_or_url: str) -> str:
    """PaddleOCR 本地识别（P2 预留）"""
    return json.dumps(
        {"error": "PaddleOCR 本地后端尚未实现，请使用 VLM 后端"},
        ensure_ascii=False
    )


async def ocr_images_batch(paths_or_urls: list) -> str:
    """批量 OCR 多张图片，合并结果"""
    results = []
    for i, item in enumerate(paths_or_urls):
        text = await ocr_image(item)
        results.append(f"--- 图片 {i+1} ---\n{text}")
    return "\n\n".join(results)
