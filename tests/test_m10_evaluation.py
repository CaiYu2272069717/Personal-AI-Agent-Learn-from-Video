"""Agent Evaluation & Observability Console tests."""

import json

import pytest


@pytest.mark.asyncio
async def test_builtin_golden_cases_cover_required_dimensions(tmp_path, monkeypatch):
    from src import database
    from src.evaluation.cases import get_builtin_cases
    from src.evaluation.service import EvaluationService

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "evaluation.db")
    await database.init_db()
    cases = get_builtin_cases()
    assert 30 <= len(cases) <= 50
    categories = {case.category for case in cases}
    assert {"completion", "citation", "tool", "security", "reliability", "observability", "comparison"} <= categories
    stored = await EvaluationService().list_cases()
    assert len(stored) == len(cases)


@pytest.mark.asyncio
async def test_offline_suite_generates_metrics_traces_and_report(tmp_path, monkeypatch):
    from src import database
    from src.evaluation.service import EvaluationService

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "evaluation.db")
    await database.init_db()
    service = EvaluationService()
    run = await service.run_suite(mode="offline", label="pytest-offline")

    assert run["status"] in {"completed", "completed_with_errors"}
    assert run["metrics"]["total"] > 0
    assert run["metrics"]["skipped"] > 0
    assert run["metrics"]["p50_latency_ms"] >= 0
    assert "categories" in run["metrics"]
    traces = await service.get_traces(run["id"])
    assert any(trace["kind"] == "guard" for trace in traces)
    assert any(trace["kind"] == "recovery" for trace in traces)
    report = await service.report_markdown(run["id"])
    assert "Agent 回归评测报告" in report
    assert "P50 延迟" in report
    assert "Token" in report
    assert "错误归因" in report


@pytest.mark.asyncio
async def test_compare_returns_quality_latency_token_cost_deltas(tmp_path, monkeypatch):
    from src import database
    from src.evaluation.service import EvaluationService

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "evaluation.db")
    await database.init_db()
    result = await EvaluationService().compare(
        baseline={"model": "model-a", "prompt_version": "v1", "rag": {"top_k": 3}},
        candidate={"model": "model-b", "prompt_version": "v2", "rag": {"top_k": 5}},
        mode="offline",
        case_ids=["compare-01", "compare-02", "compare-03", "observability-03", "observability-04"],
    )
    assert result["baseline"]["config"]["model"] == "model-a"
    assert result["candidate"]["config"]["model"] == "model-b"
    assert set(result["delta"]) == {"pass_rate", "p50_latency_ms", "p95_latency_ms", "total_tokens", "cost_usd"}


def test_metrics_percentile_and_tool_accuracy():
    from src.evaluation.metrics import evaluate_tools, percentile

    assert percentile([10, 20, 30, 40, 100], 0.5) == 30
    assert percentile([10, 20, 30, 40, 100], 0.95) == 100
    passed, details = evaluate_tools(["read_file", "list_dir"], ["read_file"])
    assert passed is True
    assert details["unexpected_tools"] == ["list_dir"]
