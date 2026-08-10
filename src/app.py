"""FastAPI 应用工厂"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import BASE_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动管线 worker + MCP 连接 + Skill 加载"""
    from .pipeline import get_pipeline
    from .config import get_config, BASE_DIR
    from .agent.mcp_client import MCPManager
    from .agent.skill_loader import SkillManager

    config = get_config()
    pipeline = get_pipeline()
    worker_task = asyncio.create_task(pipeline.start_worker())

    # 初始化 MCP
    mcp_manager = MCPManager()
    if config.mcp.enabled:
        mcp_config_path = config.mcp.config_path or str(BASE_DIR / "mcp_config.json")
        await mcp_manager.load_from_config(mcp_config_path)

    # 初始化 Skill（支持多目录）
    skill_manager = SkillManager()
    if config.skill.enabled:
        skills_dir = config.skill.skills_dir or str(BASE_DIR / "skills")
        skill_manager.load_from_directory(skills_dir)
        # 加载额外目录
        for extra_dir in config.skill.extra_dirs:
            if extra_dir:
                skill_manager.load_from_directory(extra_dir)

    # 注入到 Agent
    from .routes.agent_router import get_agent
    agent = get_agent()
    if mcp_manager.connected_servers:
        agent.tool_registry.set_mcp_manager(mcp_manager)
    if skill_manager.list_skills():
        agent.tool_registry.set_skill_manager(skill_manager)
        agent.set_skill_manager(skill_manager)

    # 存到 app.state 供其他地方访问
    app.state.mcp_manager = mcp_manager
    app.state.skill_manager = skill_manager

    yield

    # 清理
    from .agent.run_manager import get_run_manager
    await get_run_manager(agent).shutdown()
    await mcp_manager.shutdown()
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
    from .routes import (
        agent_router,
        evaluation_router,
        extensions_router,
        knowledge_router,
        pages_router,
        pipeline_router,
        settings_router,
        tools_router,
    )
    app.include_router(pages_router.router)
    app.include_router(pipeline_router.router, prefix="/api")
    app.include_router(knowledge_router.router, prefix="/api")
    app.include_router(agent_router.router, prefix="/api")
    app.include_router(agent_router.stream_router, prefix="/api")  # /api/chat/stream 直连流式
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(tools_router.router, prefix="/api")
    app.include_router(evaluation_router.router, prefix="/api")
    app.include_router(extensions_router.router, prefix="/api")

    return app
