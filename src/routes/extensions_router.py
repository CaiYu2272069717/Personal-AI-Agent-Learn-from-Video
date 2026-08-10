"""扩展能力 API 路由：MCP + Skill 状态查询与管理"""

import os
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

from ..config import get_config, save_config, reload_config, BASE_DIR

router = APIRouter(prefix="/extensions", tags=["extensions"])


@router.get("")
async def get_extensions_status(request: Request):
    """
    获取 MCP 服务器和 Skill 的完整状态信息
    """
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    skill_manager = getattr(request.app.state, "skill_manager", None)
    config = get_config()

    # MCP 服务器状态
    mcp_servers = []
    if mcp_manager:
        for name in mcp_manager.connected_servers:
            conn = mcp_manager._connections.get(name)
            tools_count = len(conn.tools) if conn else 0
            mcp_servers.append({
                "name": name,
                "status": "connected",
                "tools_count": tools_count,
                "tools": [t.name for t in conn.tools] if conn else [],
            })

    # Skill 状态
    skills_list = []
    if skill_manager:
        for skill in skill_manager.list_skills():
            skills_list.append({
                "name": skill.name,
                "display_name": skill.display_name,
                "description": skill.description,
                "triggers": skill.triggers,
                "tools_count": len(skill.tools),
                "tools": [t.get("function", {}).get("name", "") for t in skill.tools],
                "path": skill.path,
            })

    # Skill 目录配置
    primary_dir = config.skill.skills_dir or str(BASE_DIR / "skills")
    extra_dirs = config.skill.extra_dirs or []

    return {
        "mcp": {
            "enabled": config.mcp.enabled,
            "servers": mcp_servers,
        },
        "skill": {
            "enabled": config.skill.enabled,
            "auto_match": config.skill.auto_match,
            "primary_dir": primary_dir,
            "extra_dirs": extra_dirs,
            "skills": skills_list,
        },
    }


@router.get("/skills")
async def list_skills_brief(request: Request):
    """
    获取 Skill 列表（轻量版，用于聊天框斜杠命令菜单）
    """
    skill_manager = getattr(request.app.state, "skill_manager", None)
    if not skill_manager:
        return []

    result = []
    for skill in skill_manager.list_skills():
        result.append({
            "name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "triggers": skill.triggers,
        })
    return result


class AddSkillDirRequest(BaseModel):
    path: str


@router.post("/skill-dirs")
async def add_skill_dir(req: AddSkillDirRequest, request: Request):
    """
    添加一个额外的 Skill 目录
    """
    dir_path = req.path.strip()
    if not dir_path:
        return {"error": "目录路径不能为空"}

    # 验证目录存在
    if not os.path.isdir(dir_path):
        return {"error": f"目录不存在: {dir_path}"}

    config = get_config()

    # 去重
    if dir_path in config.skill.extra_dirs:
        return {"error": "该目录已添加"}

    config.skill.extra_dirs.append(dir_path)
    save_config(config)

    # 热加载：立即从新目录加载 Skill
    skill_manager = getattr(request.app.state, "skill_manager", None)
    loaded = 0
    if skill_manager:
        loaded = skill_manager.load_from_directory(dir_path)
        # 刷新 ToolRegistry
        from .agent_router import get_agent
        agent = get_agent()
        agent.tool_registry.set_skill_manager(skill_manager)

    return {
        "status": "ok",
        "path": dir_path,
        "loaded_skills": loaded,
    }


class RemoveSkillDirRequest(BaseModel):
    path: str


@router.delete("/skill-dirs")
async def remove_skill_dir(req: RemoveSkillDirRequest):
    """
    移除一个额外的 Skill 目录（不删除实际文件，重启后不再加载）
    """
    dir_path = req.path.strip()
    config = get_config()

    if dir_path not in config.skill.extra_dirs:
        return {"error": "该目录不在列表中"}

    config.skill.extra_dirs.remove(dir_path)
    save_config(config)

    return {
        "status": "ok",
        "message": f"已移除目录 {dir_path}，重启后对应 Skill 将不再加载",
    }
