"""设置页 API 回归测试。"""

import pytest

from src.config import AppConfig
from src.routes import settings_router


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"id": "model-b"}, {"id": "model-a"}]}


class _FakeClient:
    last_url = None
    last_headers = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, headers):
        type(self).last_url = url
        type(self).last_headers = headers
        return _FakeResponse()


def test_models_url_normalizes_concrete_openai_endpoints():
    assert settings_router._models_url(
        "https://api.example.com/v1/audio/transcriptions"
    ) == "https://api.example.com/v1/models"
    assert settings_router._models_url(
        "https://api.example.com/v1/"
    ) == "https://api.example.com/v1/models"


@pytest.mark.asyncio
async def test_fetch_models_reuses_configured_key_for_masked_value(monkeypatch):
    config = AppConfig()
    config.asr.base_url = "https://api.example.com/v1/audio/transcriptions"
    config.asr.api_key = "real-api-key"
    monkeypatch.setattr(settings_router, "get_config", lambda: config)
    monkeypatch.setattr(settings_router.httpx, "AsyncClient", _FakeClient)

    result = await settings_router.fetch_models(settings_router.FetchModelsRequest(
        base_url="https://api.example.com/v1/audio/transcriptions",
        api_key="real****-key",
        section="asr",
    ))

    assert result == {"models": ["model-a", "model-b"]}
    assert _FakeClient.last_url == "https://api.example.com/v1/models"
    assert _FakeClient.last_headers == {"Authorization": "Bearer real-api-key"}


@pytest.mark.asyncio
async def test_masked_key_cannot_be_sent_to_a_changed_base_url(monkeypatch):
    config = AppConfig()
    config.asr.api_key = "real-api-key"
    monkeypatch.setattr(settings_router, "get_config", lambda: config)
    monkeypatch.setattr(settings_router.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_url = None

    result = await settings_router.fetch_models(settings_router.FetchModelsRequest(
        base_url="https://untrusted.example.com/v1",
        api_key="real****-key",
        section="asr",
    ))

    assert result["models"] == []
    assert "Base URL 已修改" in result["error"]
    assert _FakeClient.last_url is None
