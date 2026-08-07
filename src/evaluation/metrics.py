"""评测指标与规则判定。"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable


URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)
CITATION_RE = re.compile(r"(?:条目|来源|source|item|id)\s*[:：#]?\s*[\w\-\u4e00-\u9fff]+", re.IGNORECASE)


def percentile(values: Iterable[float], quantile: float) -> float:
    samples = sorted(float(value) for value in values)
    if not samples:
        return 0.0
    index = max(0, min(len(samples) - 1, math.ceil(quantile * len(samples)) - 1))
    return round(samples[index], 3)


def has_citation(text: str) -> bool:
    return bool(URL_RE.search(text or "") or CITATION_RE.search(text or ""))


def evaluate_text(text: str, expected: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    value = text or ""
    reasons: list[str] = []
    min_length = expected.get("min_length")
    max_length = expected.get("max_length")
    if min_length is not None and len(value.strip()) < int(min_length):
        reasons.append(f"长度低于 {min_length}")
    if max_length is not None and len(value.strip()) > int(max_length):
        reasons.append(f"长度超过 {max_length}")
    required = expected.get("required", [])
    missing = [item for item in required if item.lower() not in value.lower()]
    if missing:
        reasons.append("缺少关键词: " + ", ".join(missing))
    required_any = expected.get("required_any", [])
    if required_any and not any(item.lower() in value.lower() for item in required_any):
        reasons.append("未命中任一关键词: " + ", ".join(required_any))
    forbidden = [item for item in expected.get("forbidden", []) if item.lower() in value.lower()]
    if forbidden:
        reasons.append("包含禁用内容: " + ", ".join(forbidden))
    if expected.get("citation_required") and not has_citation(value):
        reasons.append("缺少可识别引用")
    return not reasons, {"reasons": reasons, "length": len(value), "citation_found": has_citation(value)}


def evaluate_tools(actual_tools: list[str], expected_tools: list[str]) -> tuple[bool, dict[str, Any]]:
    actual = list(dict.fromkeys(actual_tools))
    missing = [tool for tool in expected_tools if tool not in actual]
    unexpected = [tool for tool in actual if tool not in expected_tools]
    return not missing, {"actual_tools": actual, "missing_tools": missing, "unexpected_tools": unexpected}


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for result in results if result.get("passed"))
    latencies = [float(result.get("latency_ms") or 0) for result in results]
    prompt_tokens = sum(int(result.get("prompt_tokens") or 0) for result in results)
    completion_tokens = sum(int(result.get("completion_tokens") or 0) for result in results)
    cost_usd = round(sum(float(result.get("cost_usd") or 0) for result in results), 6)
    categories: dict[str, dict[str, Any]] = {}
    for result in results:
        category = result.get("category") or "unknown"
        bucket = categories.setdefault(category, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(bool(result.get("passed")))
    for bucket in categories.values():
        bucket["rate"] = round(bucket["passed"] / bucket["total"], 4) if bucket["total"] else 0
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0,
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost_usd,
        "categories": categories,
    }
