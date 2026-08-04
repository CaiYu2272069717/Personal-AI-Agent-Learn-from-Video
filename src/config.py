"""统一配置管理：四组独立 API 配置 + 系统参数

配置优先级：config.local.json > 环境变量 > 默认值
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
WORKDIR = BASE_DIR / "workdir"
LIBRARY_DIR = BASE_DIR / "library"
PROMPTS_DIR = BASE_DIR / "prompts"
WORKSPACE_DIR = BASE_DIR / "workspace"
CONVERSATIONS_DIR = BASE_DIR / "conversations"
CONFIG_FILE = BASE_DIR / "config.local.json"


@dataclass
class ASRConfig:
    """ASR 语音识别配置"""
    base_url: str = "https://api.siliconflow.cn/v1/audio/transcriptions"
    api_key: str = ""
    model: str = "FunAudioLLM/SenseVoiceSmall"
    concurrent: int = 2


@dataclass
class LLMConfig:
    """LLM 总结配置"""
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    model: str = "deepseek-ai/DeepSeek-V3"
    temperature: float = 0.3
    max_tokens: int = 4096


@dataclass
class AgentLLMConfig:
    """Agent LLM 配置（独立于总结 LLM）"""
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    model: str = "deepseek-ai/DeepSeek-V3"
    temperature: float = 0.7
    max_tokens: int = 8192
    supports_vision: bool = False
    supports_function_calling: bool = True
    max_tool_rounds: int = 10


@dataclass
class EmbeddingConfig:
    """嵌入模型配置"""
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key: str = ""
    model: str = "BAAI/bge-m3"
    dimensions: int = 1024
    chunk_size: int = 500  # tokens per chunk
    chunk_overlap: int = 50


@dataclass
class OCRConfig:
    """OCR 配置"""
    backend: str = "vlm"  # "vlm" or "paddleocr"
    vlm_base_url: str = "https://api.siliconflow.cn/v1"
    vlm_api_key: str = ""
    vlm_model: str = "Qwen/Qwen2.5-VL-7B-Instruct"


@dataclass
class WebSearchConfig:
    """联网搜索配置"""
    backend: str = "tavily"  # "tavily" or "querit"
    tavily_api_key: str = ""
    querit_api_key: str = ""


@dataclass
class PipelineConfig:
    """流水线参数"""
    split_size_mb: int = 45  # 触发分割的文件大小阈值 MB
    split_duration_min: int = 55  # 触发分割的时长阈值（分钟）
    segment_duration_sec: int = 540  # 每段 9 分钟
    auto_delete_video: bool = True  # 全自动模式默认删除视频
    remember_delete_choice: bool = False


@dataclass
class AgentPermissionConfig:
    """Agent 权限配置"""
    full_access: bool = False  # 完全访问开关
    trusted_dirs: list = field(default_factory=list)
    command_blacklist: list = field(default_factory=lambda: [
        "rm -rf /", "rm -rf /*",
        "format", "diskpart",
        "del /s /q C:\\", "rd /s /q C:\\",
        "reg delete HKLM",
    ])
    command_timeout: int = 60  # 秒，超时转后台


@dataclass
class AppConfig:
    """应用总配置"""
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent_llm: AgentLLMConfig = field(default_factory=AgentLLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    agent_permission: AgentPermissionConfig = field(default_factory=AgentPermissionConfig)
    host: str = "127.0.0.1"
    port: int = 8000


def _deep_update(base: dict, override: dict) -> dict:
    """递归合并字典"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def _load_from_file(path: Path) -> dict:
    """从 JSON 配置文件加载"""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_from_env() -> dict:
    """从环境变量加载（仅 API Key 类敏感信息）"""
    env_map = {}
    if os.getenv("ASR_API_KEY"):
        env_map.setdefault("asr", {})["api_key"] = os.getenv("ASR_API_KEY")
    if os.getenv("LLM_API_KEY"):
        env_map.setdefault("llm", {})["api_key"] = os.getenv("LLM_API_KEY")
    if os.getenv("AGENT_LLM_API_KEY"):
        env_map.setdefault("agent_llm", {})["api_key"] = os.getenv("AGENT_LLM_API_KEY")
    if os.getenv("EMBEDDING_API_KEY"):
        env_map.setdefault("embedding", {})["api_key"] = os.getenv("EMBEDDING_API_KEY")
    if os.getenv("TAVILY_API_KEY"):
        env_map.setdefault("web_search", {})["tavily_api_key"] = os.getenv("TAVILY_API_KEY")
    if os.getenv("OCR_API_KEY"):
        env_map.setdefault("ocr", {})["vlm_api_key"] = os.getenv("OCR_API_KEY")
    return env_map


def load_config() -> AppConfig:
    """加载完整配置（文件 > 环境变量 > 默认值）"""
    base = asdict(AppConfig())
    file_cfg = _load_from_file(CONFIG_FILE)
    env_cfg = _load_from_env()

    _deep_update(base, file_cfg)
    _deep_update(base, env_cfg)

    # 重建 dataclass
    return AppConfig(
        asr=ASRConfig(**base["asr"]),
        llm=LLMConfig(**base["llm"]),
        agent_llm=AgentLLMConfig(**base["agent_llm"]),
        embedding=EmbeddingConfig(**base["embedding"]),
        ocr=OCRConfig(**base["ocr"]),
        web_search=WebSearchConfig(**base["web_search"]),
        pipeline=PipelineConfig(**base["pipeline"]),
        agent_permission=AgentPermissionConfig(**base["agent_permission"]),
        host=base["host"],
        port=base["port"],
    )


def save_config(config: AppConfig) -> None:
    """保存配置到文件（不含默认值中未修改的环境变量）"""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 全局单例
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    """重新加载配置"""
    global _config
    _config = load_config()
    return _config
