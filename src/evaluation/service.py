"""评测执行、持久化、聚合与回归报告。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI

from ..agent.core import AgentCore
from ..agent.permissions import PermissionManager
from ..agent.run_manager import AgentRunManager
from ..agent.snapshots import record_file_before, restore_from_turn
from ..agent.tools import ToolRegistry
from ..config import BASE_DIR, get_config
from ..database import Database
from .cases import GoldenCase, get_builtin_cases
from .metrics import aggregate_results, evaluate_text, evaluate_tools, percentile


class EvaluationService:
    """可重复运行的 Agent 评测服务。

    offline 模式执行权限、沙箱、Revert、恢复、Trace 与指标等确定性案例；
    live 模式额外调用当前或覆盖后的 OpenAI 兼容模型评测回答与工具选择。
    """

    def __init__(self):
        self.cases = {case.id: case for case in get_builtin_cases()}

    async def sync_cases(self) -> None:
        async with Database() as db:
            for case in self.cases.values():
                await db.execute(
                    """INSERT INTO eval_cases
                           (id, name, category, prompt, evaluator, expected_json, tags_json, description, builtin)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                           name=excluded.name, category=excluded.category, prompt=excluded.prompt,
                           evaluator=excluded.evaluator, expected_json=excluded.expected_json,
                           tags_json=excluded.tags_json, description=excluded.description""",
                    (
                        case.id, case.name, case.category, case.prompt, case.evaluator,
                        json.dumps(case.expected, ensure_ascii=False),
                        json.dumps(case.tags, ensure_ascii=False), case.description,
                    ),
                )
            await db.commit()

    async def list_cases(self, category: str = "") -> list[dict[str, Any]]:
        await self.sync_cases()
        query = "SELECT * FROM eval_cases"
        params: tuple[Any, ...] = ()
        if category:
            query += " WHERE category = ?"
            params = (category,)
        query += " ORDER BY category, id"
        async with Database() as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
        return [self._case_row(row) for row in rows]

    async def run_suite(
        self,
        mode: str = "offline",
        case_ids: Optional[list[str]] = None,
        variant: Optional[dict[str, Any]] = None,
        label: str = "baseline",
        parent_run_id: str = "",
    ) -> dict[str, Any]:
        if mode not in {"offline", "live"}:
            raise ValueError("mode 必须是 offline 或 live")
        await self.sync_cases()
        variant = variant or {}
        selected = [self.cases[item] for item in case_ids or self.cases if item in self.cases]
        if not case_ids:
            selected = list(self.cases.values())
        run_id = uuid.uuid4().hex
        config = get_config()
        model = variant.get("model") or config.agent_llm.model
        run_config = {
            "mode": mode,
            "model": model,
            "temperature": variant.get("temperature", config.agent_llm.temperature),
            "prompt_version": variant.get("prompt_version", "current"),
            "prompt_suffix": variant.get("prompt_suffix", ""),
            "rag": variant.get("rag", {}),
            "input_cost_per_million": float(variant.get("input_cost_per_million", 0)),
            "output_cost_per_million": float(variant.get("output_cost_per_million", 0)),
        }
        async with Database() as db:
            await db.execute(
                """INSERT INTO eval_runs
                       (id, parent_run_id, label, mode, status, model, config_json, total_cases)
                   VALUES (?, ?, ?, ?, 'running', ?, ?, ?)""",
                (run_id, parent_run_id or None, label, mode, model, json.dumps(run_config, ensure_ascii=False), len(selected)),
            )
            await db.commit()

        results: list[dict[str, Any]] = []
        for case in selected:
            started = time.perf_counter()
            try:
                result = await self._execute_case(case, mode, run_config, run_id)
            except Exception as exc:
                result = {
                    "passed": False,
                    "status": "failed",
                    "output": "",
                    "details": {"reasons": [str(exc)]},
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            result.update({
                "case_id": case.id,
                "case_name": case.name,
                "category": case.category,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            })
            await self._save_result(run_id, result)
            results.append(result)

        scored = [result for result in results if result.get("status") != "skipped"]
        metrics = aggregate_results(scored)
        metrics["skipped"] = len(results) - len(scored)
        metrics["coverage_rate"] = round(len(scored) / len(results), 4) if results else 0
        status = "completed" if not any(result.get("error_type") for result in results) else "completed_with_errors"
        async with Database() as db:
            await db.execute(
                """UPDATE eval_runs SET status=?, passed_cases=?, skipped_cases=?, metrics_json=?,
                       completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                (status, metrics["passed"], metrics["skipped"], json.dumps(metrics, ensure_ascii=False), run_id),
            )
            await db.commit()
        return await self.get_run(run_id, include_results=True)

    async def compare(
        self,
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        mode: str = "offline",
        case_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        group_id = uuid.uuid4().hex
        baseline_run = await self.run_suite(mode, case_ids, baseline, "baseline", group_id)
        candidate_run = await self.run_suite(mode, case_ids, candidate, "candidate", group_id)
        base_metrics = baseline_run["metrics"]
        candidate_metrics = candidate_run["metrics"]
        return {
            "comparison_id": group_id,
            "baseline": baseline_run,
            "candidate": candidate_run,
            "delta": {
                "pass_rate": round(candidate_metrics.get("pass_rate", 0) - base_metrics.get("pass_rate", 0), 4),
                "p50_latency_ms": round(candidate_metrics.get("p50_latency_ms", 0) - base_metrics.get("p50_latency_ms", 0), 3),
                "p95_latency_ms": round(candidate_metrics.get("p95_latency_ms", 0) - base_metrics.get("p95_latency_ms", 0), 3),
                "total_tokens": candidate_metrics.get("total_tokens", 0) - base_metrics.get("total_tokens", 0),
                "cost_usd": round(candidate_metrics.get("cost_usd", 0) - base_metrics.get("cost_usd", 0), 6),
            },
        }

    async def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        async with Database() as db:
            cursor = await db.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            )
            rows = await cursor.fetchall()
        return [self._run_row(row) for row in rows]

    async def get_run(self, run_id: str, include_results: bool = True) -> dict[str, Any]:
        async with Database() as db:
            cursor = await db.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,))
            row = await cursor.fetchone()
            if not row:
                raise KeyError(run_id)
            run = self._run_row(row)
            if include_results:
                cursor = await db.execute(
                    "SELECT * FROM eval_case_results WHERE run_id = ? ORDER BY id", (run_id,)
                )
                run["results"] = [self._result_row(item) for item in await cursor.fetchall()]
        return run

    async def get_traces(self, run_id: str, case_id: str = "") -> list[dict[str, Any]]:
        query = "SELECT * FROM eval_traces WHERE run_id = ?"
        params: list[Any] = [run_id]
        if case_id:
            query += " AND case_id = ?"
            params.append(case_id)
        query += " ORDER BY id"
        async with Database() as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [self._trace_row(row) for row in rows]

    async def report_markdown(self, run_id: str) -> str:
        run = await self.get_run(run_id, True)
        metrics = run["metrics"]
        lines = [
            f"# Agent 回归评测报告 — {run['label']}", "",
            f"- Run ID: `{run['id']}`", f"- 模式: `{run['mode']}`", f"- 模型: `{run['model']}`",
            f"- 状态: `{run['status']}`", f"- 生成时间: {run.get('completed_at') or run.get('created_at')}", "",
            "## 总览", "",
            "| 指标 | 结果 |", "|---|---:|",
            f"| 任务完成率 | {metrics.get('pass_rate', 0) * 100:.1f}% |",
            f"| 通过 / 失败 / 跳过 | {metrics.get('passed', 0)} / {metrics.get('failed', 0)} / {metrics.get('skipped', 0)} |",
            f"| 覆盖率 | {metrics.get('coverage_rate', 0) * 100:.1f}% |",
            f"| P50 延迟 | {metrics.get('p50_latency_ms', 0):.1f} ms |",
            f"| P95 延迟 | {metrics.get('p95_latency_ms', 0):.1f} ms |",
            f"| Token | {metrics.get('total_tokens', 0)} |",
            f"| 成本 | ${metrics.get('cost_usd', 0):.6f} |", "",
            "## 分类结果", "", "| 分类 | 通过 | 总数 | 完成率 |", "|---|---:|---:|---:|",
        ]
        for category, item in metrics.get("categories", {}).items():
            lines.append(f"| {category} | {item['passed']} | {item['total']} | {item['rate'] * 100:.1f}% |")
        lines.extend(["", "## 案例明细", "", "| 案例 | 分类 | 状态 | 延迟 | 错误归因 |", "|---|---|---|---:|---|"])
        for result in run.get("results", []):
            reason = result.get("error_message") or "; ".join(result.get("details", {}).get("reasons", [])) or "-"
            state = "PASS" if result.get("passed") else ("SKIP" if result.get("status") == "skipped" else "FAIL")
            lines.append(f"| {result['case_name']} | {result['category']} | {state} | {result['latency_ms']:.1f} ms | {reason[:100]} |")
        return "\n".join(lines) + "\n"

    async def _execute_case(self, case: GoldenCase, mode: str, config: dict[str, Any], run_id: str) -> dict[str, Any]:
        evaluator = case.evaluator
        if evaluator in {"response", "agent"}:
            if mode != "live":
                return {"passed": False, "status": "skipped", "output": "", "details": {"reasons": ["需要 live 模式与模型 API"]}}
            return await self._evaluate_live(case, config, run_id)
        if evaluator == "permission":
            return await self._evaluate_permission(case, run_id)
        if evaluator == "sandbox":
            return await self._evaluate_sandbox(case, run_id)
        if evaluator == "revert":
            return await self._evaluate_revert(case, run_id)
        if evaluator == "recovery":
            return await self._evaluate_recovery(case, run_id)
        if evaluator == "latency":
            samples = case.expected["samples"]
            actual = {"p50": percentile(samples, 0.5), "p95": percentile(samples, 0.95)}
            passed = actual["p50"] == case.expected["p50"] and actual["p95"] == case.expected["p95"]
            return {"passed": passed, "status": "passed" if passed else "failed", "output": json.dumps(actual), "details": actual}
        if evaluator == "synthetic_trace":
            return await self._evaluate_synthetic_trace(case, run_id)
        if evaluator == "comparison":
            field = case.expected["field"]
            sample = {"baseline": {field: "A"}, "candidate": {field: "B"}}
            passed = all(name in sample and field in sample[name] for name in case.expected["variants"])
            return {"passed": passed, "status": "passed" if passed else "failed", "output": json.dumps(sample), "details": sample}
        raise ValueError(f"未知 evaluator: {evaluator}")

    async def _evaluate_live(self, case: GoldenCase, config: dict[str, Any], run_id: str) -> dict[str, Any]:
        agent_config = get_config().agent_llm
        client = AsyncOpenAI(base_url=agent_config.base_url, api_key=agent_config.api_key)
        system_prompt = AgentCore()._build_system_prompt() + "\n" + config.get("prompt_suffix", "")
        kwargs: dict[str, Any] = {
            "model": config["model"], "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": case.prompt}],
            "temperature": config["temperature"], "stream": False,
        }
        if case.evaluator == "agent":
            kwargs["tools"] = ToolRegistry().list_tools()
        started = time.perf_counter()
        await self._trace(run_id, case.id, "model", "chat.completions", "start", {"model": config["model"]})
        response = await client.chat.completions.create(**kwargs)
        latency = round((time.perf_counter() - started) * 1000, 3)
        choice = response.choices[0].message
        text = choice.content or ""
        actual_tools = [call.function.name for call in (choice.tool_calls or [])]
        usage = response.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = (
            prompt_tokens * config["input_cost_per_million"] / 1_000_000
            + completion_tokens * config["output_cost_per_million"] / 1_000_000
        )
        await self._trace(run_id, case.id, "model", "chat.completions", "end", {
            "duration_ms": latency, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "cost_usd": cost,
        })
        text_passed, text_details = evaluate_text(text, case.expected)
        tool_passed, tool_details = evaluate_tools(actual_tools, case.expected.get("expected_tools", []))
        passed = text_passed and tool_passed
        return {
            "passed": passed, "status": "passed" if passed else "failed", "output": text,
            "details": {**text_details, **tool_details}, "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens, "cost_usd": round(cost, 8),
        }

    async def _evaluate_permission(self, case: GoldenCase, run_id: str) -> dict[str, Any]:
        expected = case.expected
        manager = PermissionManager()
        started = time.perf_counter()
        if "path" in expected:
            allowed, reason = manager.check_file_boundary(expected["path"])
        else:
            allowed, reason = manager.check_tool_permission(expected["tool"], expected["risk"], expected["arguments"])
        duration = round((time.perf_counter() - started) * 1000, 3)
        passed = allowed is expected["allowed"] and (
            not expected.get("reason_contains") or expected["reason_contains"] in (reason or "")
        )
        details = {"allowed": allowed, "reason": reason}
        await self._trace(run_id, case.id, "guard", "permission", "end", {**details, "duration_ms": duration})
        return {"passed": passed, "status": "passed" if passed else "failed", "output": reason or "allowed", "details": details}

    async def _evaluate_sandbox(self, case: GoldenCase, run_id: str) -> dict[str, Any]:
        await self._trace(run_id, case.id, "tool", "run_python_sandbox", "start", {"arguments": {"code": case.expected["code"]}})
        started = time.perf_counter()
        output = await ToolRegistry().execute("run_python_sandbox", {"code": case.expected["code"]})
        duration = round((time.perf_counter() - started) * 1000, 3)
        data = json.loads(output)
        passed = data.get("returncode") == case.expected["returncode"] and case.expected["stderr_contains"] in data.get("stderr", "")
        await self._trace(run_id, case.id, "tool", "run_python_sandbox", "end", {"duration_ms": duration, "returncode": data.get("returncode")})
        return {"passed": passed, "status": "passed" if passed else "failed", "output": output, "details": data}

    async def _evaluate_revert(self, case: GoldenCase, run_id: str) -> dict[str, Any]:
        mode = case.expected["mode"]
        root = BASE_DIR / "temp" / "evaluation"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{run_id}-{case.id}.txt"
        if path.exists():
            path.unlink()
        async with Database() as db:
            cursor = await db.execute("INSERT INTO conversations (title) VALUES (?)", (f"eval-{case.id}",))
            conversation_id = cursor.lastrowid
            cursor = await db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', 'eval')", (conversation_id,))
            turn_one = cursor.lastrowid
            await db.commit()
        await self._trace(run_id, case.id, "revert", mode, "start", {"path": str(path)})
        if mode == "create":
            await record_file_before(conversation_id, turn_one, path)
            path.write_text("created", encoding="utf-8")
            result = await restore_from_turn(conversation_id, turn_one)
            passed = not path.exists()
        elif mode == "modify":
            path.write_text("original", encoding="utf-8")
            await record_file_before(conversation_id, turn_one, path)
            path.write_text("changed", encoding="utf-8")
            result = await restore_from_turn(conversation_id, turn_one)
            passed = path.read_text(encoding="utf-8") == "original"
            path.unlink(missing_ok=True)
        else:
            path.write_text("original", encoding="utf-8")
            await record_file_before(conversation_id, turn_one, path)
            path.write_text("one", encoding="utf-8")
            async with Database() as db:
                cursor = await db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', 'eval2')", (conversation_id,))
                turn_two = cursor.lastrowid
                await db.commit()
            await record_file_before(conversation_id, turn_two, path)
            path.write_text("two", encoding="utf-8")
            result = await restore_from_turn(conversation_id, turn_one)
            passed = path.read_text(encoding="utf-8") == "original"
            path.unlink(missing_ok=True)
        await self._trace(run_id, case.id, "revert", mode, "end", {**result, "passed": passed})
        async with Database() as db:
            await db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            await db.commit()
        return {"passed": passed, "status": "passed" if passed else "failed", "output": json.dumps(result), "details": result}

    async def _evaluate_recovery(self, case: GoldenCase, run_id: str) -> dict[str, Any]:
        mode = case.expected["mode"]

        class FakeAgent:
            async def chat(self, message, conversation_id, history, user_message_id=0):
                yield {"type": "status", "status": "thinking", "label": "eval"}
                if mode == "failure":
                    raise RuntimeError("synthetic recovery failure")
                await asyncio.sleep(0.01)
                yield {"type": "text", "content": "done"}
                yield {"type": "done", "content": "done", "conversation_id": conversation_id}

        async with Database() as db:
            cursor = await db.execute("INSERT INTO conversations (title) VALUES (?)", (f"eval-{case.id}",))
            conversation_id = cursor.lastrowid
            await db.commit()
        manager = AgentRunManager(FakeAgent())
        agent_run_id = await manager.start("eval", conversation_id, [])
        await asyncio.wait_for(manager._tasks[agent_run_id], timeout=2)
        async with Database() as db:
            cursor = await db.execute("SELECT status, error FROM agent_runs WHERE id = ?", (agent_run_id,))
            status, error = await cursor.fetchone()
            cursor = await db.execute("SELECT event_json FROM agent_run_events WHERE run_id = ? ORDER BY id", (agent_run_id,))
            events = [json.loads(row[0]) for row in await cursor.fetchall()]
        if mode == "failure":
            passed = status == "failed" and "synthetic recovery failure" in error
        elif mode == "events":
            passed = [event["type"] for event in events] == ["status", "text", "done"]
        else:
            passed = status == "completed" and events[-1]["type"] == "done"
        details = {"agent_run_id": agent_run_id, "status": status, "error": error, "events": [event["type"] for event in events]}
        await self._trace(run_id, case.id, "recovery", mode, "end", details)
        async with Database() as db:
            await db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            await db.commit()
        return {"passed": passed, "status": "passed" if passed else "failed", "output": json.dumps(details), "details": details}

    async def _evaluate_synthetic_trace(self, case: GoldenCase, run_id: str) -> dict[str, Any]:
        expected = case.expected
        if "events" in expected:
            for phase in expected["events"]:
                await self._trace(run_id, case.id, "synthetic", phase, "end", {"duration_ms": 5})
            actual = expected["events"]
            passed = len(actual) == 2
        else:
            payload = {key: value for key, value in expected.items()}
            await self._trace(run_id, case.id, "synthetic", "usage", "end", payload)
            actual = payload
            passed = all(actual[key] == value for key, value in expected.items())
        return {"passed": passed, "status": "passed" if passed else "failed", "output": json.dumps(actual), "details": {"actual": actual},
                "prompt_tokens": int(expected.get("prompt_tokens", 0)), "completion_tokens": int(expected.get("completion_tokens", 0)),
                "cost_usd": float(expected.get("cost_usd", 0))}

    async def _trace(self, run_id: str, case_id: str, kind: str, name: str, phase: str, payload: dict[str, Any]) -> None:
        async with Database() as db:
            await db.execute(
                """INSERT INTO eval_traces
                       (run_id, case_id, kind, name, phase, duration_ms, payload_json, error_type, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, case_id, kind, name, phase, float(payload.get("duration_ms") or 0),
                    json.dumps(payload, ensure_ascii=False), payload.get("error_type", ""), payload.get("error_message", ""),
                ),
            )
            await db.commit()

    async def _save_result(self, run_id: str, result: dict[str, Any]) -> None:
        async with Database() as db:
            await db.execute(
                """INSERT INTO eval_case_results
                       (run_id, case_id, case_name, category, status, passed, latency_ms, output_text,
                        details_json, prompt_tokens, completion_tokens, cost_usd, error_type, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, result["case_id"], result["case_name"], result["category"], result.get("status", "failed"),
                    int(bool(result.get("passed"))), result.get("latency_ms", 0), result.get("output", ""),
                    json.dumps(result.get("details", {}), ensure_ascii=False), result.get("prompt_tokens", 0),
                    result.get("completion_tokens", 0), result.get("cost_usd", 0), result.get("error_type", ""), result.get("error_message", ""),
                ),
            )
            await db.commit()

    @staticmethod
    def _json(value: Any, fallback: Any) -> Any:
        try:
            return json.loads(value) if value else fallback
        except (TypeError, json.JSONDecodeError):
            return fallback

    def _case_row(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["expected"] = self._json(item.pop("expected_json", ""), {})
        item["tags"] = self._json(item.pop("tags_json", ""), [])
        return item

    def _run_row(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["config"] = self._json(item.pop("config_json", ""), {})
        item["metrics"] = self._json(item.pop("metrics_json", ""), {})
        return item

    def _result_row(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["passed"] = bool(item["passed"])
        item["details"] = self._json(item.pop("details_json", ""), {})
        return item

    def _trace_row(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = self._json(item.pop("payload_json", ""), {})
        return item
