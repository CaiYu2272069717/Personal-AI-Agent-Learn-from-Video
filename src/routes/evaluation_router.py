"""Agent 评测与可观测性 API。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..evaluation.service import EvaluationService


router = APIRouter(prefix="/evaluation", tags=["evaluation"])
_service: Optional[EvaluationService] = None


def get_evaluation_service() -> EvaluationService:
    global _service
    if _service is None:
        _service = EvaluationService()
    return _service


class RunRequest(BaseModel):
    mode: str = Field(default="offline", pattern="^(offline|live)$")
    case_ids: list[str] = Field(default_factory=list)
    label: str = "baseline"
    variant: dict[str, Any] = Field(default_factory=dict)


class CompareRequest(BaseModel):
    mode: str = Field(default="offline", pattern="^(offline|live)$")
    case_ids: list[str] = Field(default_factory=list)
    baseline: dict[str, Any] = Field(default_factory=dict)
    candidate: dict[str, Any] = Field(default_factory=dict)


@router.get("/cases")
async def list_cases(category: str = ""):
    cases = await get_evaluation_service().list_cases(category)
    categories = sorted({case["category"] for case in cases})
    return {"cases": cases, "categories": categories, "count": len(cases)}


@router.post("/runs")
async def create_run(req: RunRequest):
    try:
        return await get_evaluation_service().run_suite(
            mode=req.mode,
            case_ids=req.case_ids or None,
            variant=req.variant,
            label=req.label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs")
async def list_runs(limit: int = Query(default=30, ge=1, le=100)):
    return {"runs": await get_evaluation_service().list_runs(limit)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    try:
        return await get_evaluation_service().get_run(run_id, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="评测运行不存在") from exc


@router.get("/runs/{run_id}/traces")
async def get_traces(run_id: str, case_id: str = ""):
    return {"traces": await get_evaluation_service().get_traces(run_id, case_id)}


@router.get("/runs/{run_id}/report", response_class=PlainTextResponse)
async def get_report(run_id: str):
    try:
        content = await get_evaluation_service().report_markdown(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="评测运行不存在") from exc
    return PlainTextResponse(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="agent-eval-{run_id[:8]}.md"'},
    )


@router.post("/compare")
async def compare(req: CompareRequest):
    try:
        return await get_evaluation_service().compare(
            baseline=req.baseline,
            candidate=req.candidate,
            mode=req.mode,
            case_ids=req.case_ids or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
