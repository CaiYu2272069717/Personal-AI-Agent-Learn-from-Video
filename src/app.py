"""FastAPI 应用工厂"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import BASE_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动管线 worker"""
    from .pipeline import get_pipeline
    pipeline = get_pipeline()
    worker_task = asyncio.create_task(pipeline.start_worker())
    yield
    from .routes.agent_router import get_agent
    from .agent.run_manager import get_run_manager
    await get_run_manager(get_agent()).shutdown()
    await pipeline.stop()
    worker_task.cancel()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用"""
    app = FastAPI(
        title="Learn From Video",
        description="短视频学习总结系统",
        version="0.3.0",
        lifespan=lifespan,
    )

    # 静态文件
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 注册路由
    from .routes import pipeline_router, knowledge_router, agent_router, settings_router, pages_router, tools_router
    app.include_router(pages_router.router)
    app.include_router(pipeline_router.router, prefix="/api")
    app.include_router(knowledge_router.router, prefix="/api")
    app.include_router(agent_router.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(tools_router.router, prefix="/api")

    return app
