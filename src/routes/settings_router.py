"""设置 API 路由"""

import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit

from ..config import get_config, save_config, reload_config, AppConfig

router = APIRouter(prefix="/settings", tags=["settings"])


class FetchModelsRequest(BaseModel):
    base_url: str
    api_key: str
    section: str | None = None


def _models_url(base_url: str) -> str:
    """把 OpenAI 兼容接口地址归一化为模型列表地址。"""
    parts = urlsplit(base_url.strip())
    path = parts.path.rstrip('/')

    # ASR 配置保存的是具体的转写端点，而不是 API 根地址。
    for endpoint in ('/audio/transcriptions', '/chat/completions', '/embeddings'):
        if path.endswith(endpoint):
            path = path[:-len(endpoint)]
            break

    if not path.endswith('/models'):
        path += '/models'
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def _configured_connection(section: str | None) -> tuple[str, str]:
    """读取指定配置区的原始地址与 Key，用于替换设置页中的脱敏值。"""
    config = get_config()
    connections = {
        'asr': (config.asr.base_url, config.asr.api_key),
        'llm': (config.llm.base_url, config.llm.api_key),
        'agent_llm': (config.agent_llm.base_url, config.agent_llm.api_key),
        'embedding': (config.embedding.base_url, config.embedding.api_key),
        'ocr': (config.ocr.vlm_base_url, config.ocr.vlm_api_key),
    }
    return connections.get(section or '', ('', ''))


@router.post("/models")
async def fetch_models(req: FetchModelsRequest):
    """从 OpenAI 兼容 API 获取可用模型列表"""
    api_key = req.api_key
    if '****' in api_key:
        configured_url, api_key = _configured_connection(req.section)
        if req.base_url.rstrip('/') != configured_url.rstrip('/'):
            return {
                "models": [],
                "error": "Base URL 已修改，请重新填写对应的 API Key",
            }
    if not api_key:
        return {"models": [], "error": "请先配置 API Key"}

    url = _models_url(req.base_url)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={
                'Authorization': f'Bearer {api_key}',
            })
            resp.raise_for_status()
            data = resp.json()
            models = [m['id'] for m in data.get('data', [])]
            models.sort()
            return {"models": models}
    except httpx.TimeoutException:
        return {"models": [], "error": "请求超时"}
    except httpx.HTTPStatusError as e:
        return {"models": [], "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"models": [], "error": str(e)}


@router.get("")
async def get_settings():
    """获取当前配置（隐藏 API Key 中间部分）"""
    config = get_config()
    data = asdict(config)
    # 掩码 API Key
    for section in ["asr", "llm", "agent_llm", "embedding", "ocr", "web_search"]:
        if section in data:
            for key in list(data[section].keys()):
                if "key" in key.lower() and data[section][key]:
                    val = data[section][key]
                    data[section][key] = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
    return data


@router.put("")
async def update_settings(payload: dict):
    """更新配置"""
    config = get_config()
    current = asdict(config)

    # 检测完全访问开关是否由关变开
    old_full_access = current.get("agent_permission", {}).get("full_access", False)
    new_full_access = old_full_access
    if isinstance(payload.get("agent_permission"), dict):
        new_full_access = payload["agent_permission"].get("full_access", old_full_access)

    # 深度合并
    for section, values in payload.items():
        if isinstance(values, dict) and section in current:
            for k, v in values.items():
                if k in current[section]:
                    # 跳过掩码值
                    if "key" in k.lower() and "****" in str(v):
                        continue
                    current[section][k] = v
        elif section in current:
            current[section] = values

    # 重建并保存
    from ..config import ASRConfig, LLMConfig, AgentLLMConfig, EmbeddingConfig, OCRConfig, WebSearchConfig, PipelineConfig, AgentPermissionConfig
    new_config = AppConfig(
        asr=ASRConfig(**current["asr"]),
        llm=LLMConfig(**current["llm"]),
        agent_llm=AgentLLMConfig(**current["agent_llm"]),
        embedding=EmbeddingConfig(**current["embedding"]),
        ocr=OCRConfig(**current["ocr"]),
        web_search=WebSearchConfig(**current["web_search"]),
        pipeline=PipelineConfig(**current["pipeline"]),
        agent_permission=AgentPermissionConfig(**current["agent_permission"]),
        host=current["host"],
        port=current["port"],
    )
    save_config(new_config)
    reload_config()

    # 若用户刚刚开启完全访问，自动批准当前所有待确认的工具请求
    if not old_full_access and new_full_access:
        from .agent_router import get_agent
        get_agent().auto_approve_all_pending()

    return {"status": "ok"}
