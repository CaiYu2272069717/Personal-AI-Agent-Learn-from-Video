"""ASR 转录模块：OpenAI 兼容接口

支持可配 base_url/key/model，分段并发转录，合并，重试。
"""

import asyncio
from pathlib import Path
from typing import List, Optional, Callable

import httpx

from .config import get_config


class ASRClient:
    """语音识别客户端"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        concurrent: Optional[int] = None,
    ):
        config = get_config().asr
        self.base_url = (base_url or config.base_url).rstrip('/')
        self.api_key = api_key or config.api_key
        self.model = model or config.model
        self.concurrent = concurrent or config.concurrent

        if not self.api_key:
            raise ValueError("ASR API Key 未配置，请在设置页或环境变量中设置")

    async def transcribe_file(
        self,
        audio_path: Path,
        context: Optional[str] = None,
        max_retries: int = 3,
    ) -> str:
        """转录单个音频文件

        Args:
            audio_path: 音频文件路径
            context: 上下文（提升专有名词准确率）
            max_retries: 最大重试次数

        Returns:
            转录文本
        """
        # 确定 endpoint
        endpoint = self.base_url
        if not endpoint.endswith('/transcriptions'):
            endpoint = f"{endpoint}/audio/transcriptions"

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    with open(audio_path, 'rb') as f:
                        files = {'file': (audio_path.name, f, 'audio/mpeg')}
                        data = {'model': self.model}
                        if context:
                            data['prompt'] = context

                        response = await client.post(
                            endpoint,
                            files=files,
                            data=data,
                            headers={'Authorization': f'Bearer {self.api_key}'},
                        )

                if response.status_code != 200:
                    raise RuntimeError(
                        f"ASR 请求失败 (HTTP {response.status_code}): {response.text[:200]}"
                    )

                result = response.json()
                text = result.get('text', '')
                if not text:
                    raise RuntimeError(f"ASR 返回空文本: {response.text[:200]}")
                return text

            except Exception as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"ASR 转录失败（重试 {max_retries} 次）: {e}")
                await asyncio.sleep(1 * (attempt + 1))

        return ""  # unreachable

    async def transcribe_segments(
        self,
        audio_paths: List[Path],
        context: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> str:
        """并发转录多个音频片段，按序合并

        Args:
            audio_paths: 分段音频列表
            context: 上下文
            progress_callback: 进度回调 (completed, total)

        Returns:
            合并后的完整转录文本
        """
        total = len(audio_paths)
        results = [''] * total
        completed = 0

        semaphore = asyncio.Semaphore(self.concurrent)

        async def _transcribe_one(index: int, path: Path):
            nonlocal completed
            async with semaphore:
                text = await self.transcribe_file(path, context=context)
                results[index] = text
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        tasks = [
            asyncio.create_task(_transcribe_one(i, p))
            for i, p in enumerate(audio_paths)
        ]
        await asyncio.gather(*tasks)

        return ''.join(results)
