"""Agent 联网工具：web_search + web_fetch"""

import json
from typing import Optional

import httpx

from ..config import get_config


async def web_search(query: str) -> str:
    """联网搜索（Tavily API）

    返回结构化结果列表。
    """
    config = get_config().web_search
    api_key = config.tavily_api_key

    if not api_key:
        return json.dumps({"error": "联网搜索未配置 API Key"}, ensure_ascii=False)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 5,
                    "include_answer": True,
                },
            )

        if response.status_code != 200:
            return json.dumps({"error": f"搜索失败: {response.status_code}"}, ensure_ascii=False)

        data = response.json()
        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:300],
            })

        output = {
            "answer": data.get("answer", ""),
            "results": results,
        }
        return json.dumps(output, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"搜索异常: {str(e)}"}, ensure_ascii=False)


async def web_fetch(url: str) -> str:
    """抓取网页正文转 Markdown"""
    try:
        import trafilatura

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)

        if response.status_code != 200:
            return json.dumps({"error": f"抓取失败: {response.status_code}"}, ensure_ascii=False)

        text = trafilatura.extract(
            response.text,
            output_format='markdown',
            include_comments=False,
            include_tables=True,
        )

        if not text:
            return json.dumps({"error": "无法提取正文内容"}, ensure_ascii=False)

        # 截断过长内容
        if len(text) > 5000:
            text = text[:5000] + "\n\n... (内容过长，已截断)"

        return json.dumps({"url": url, "content": text}, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"抓取异常: {str(e)}"}, ensure_ascii=False)
