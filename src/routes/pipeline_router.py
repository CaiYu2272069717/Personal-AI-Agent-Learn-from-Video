"""流水线任务 API 路由"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json
import asyncio

from ..pipeline import get_pipeline, Stage
from ..database import Database

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class SubmitRequest(BaseModel):
    url: str = ""
    file_path: str = ""
    mode: str = "auto"
    exit_stage: int = 7
    template: str = "knowledge"


@router.post("/submit")
async def submit_task(req: SubmitRequest):
    """提交处理任务"""
    pipeline = get_pipeline()
    task_id = await pipeline.submit(
        input_text=req.url,
        input_file=req.file_path,
        mode=req.mode,
        exit_stage=req.exit_stage,
        template=req.template,
    )
    return {"status": "ok", "task_id": task_id}


@router.get("/tasks")
async def list_tasks():
    """获取任务列表"""
    async with Database() as db:
        cursor = await db.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
        tasks = [dict(r) for r in rows]
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
async def get_task(task_id: int):
    """获取单个任务状态"""
    async with Database() as db:
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
    if not row:
        return {"task_id": task_id, "status": "not_found"}
    return dict(row)


@router.get("/tasks/{task_id}/stream")
async def stream_task_progress(task_id: int):
    """SSE 流式推送任务进度"""
    async def event_generator():
        prev_stage = 0
        while True:
            async with Database() as db:
                cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                row = await cursor.fetchone()

            if not row:
                yield f"data: {json.dumps({'type': 'error', 'message': 'task not found'})}\n\n"
                break

            task = dict(row)
            if task['current_stage'] != prev_stage:
                prev_stage = task['current_stage']
                stage_name = Stage(task['current_stage']).name if task['current_stage'] > 0 else 'queued'
                yield f"data: {json.dumps({'type': 'progress', 'stage': task['current_stage'], 'stage_name': stage_name, 'status': task['status']})}\n\n"

            if task['status'] in ('completed', 'failed'):
                yield f"data: {json.dumps({'type': 'done', 'status': task['status'], 'error': task.get('error', '')})}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
