"""页面路由（Jinja2 渲染）"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import BASE_DIR

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页 - Agent 对话页"""
    return templates.TemplateResponse(request=request, name="chat.html")


@router.get("/submit", response_class=HTMLResponse)
async def submit_page(request: Request):
    """提交页"""
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    """知识库页"""
    return templates.TemplateResponse(request=request, name="library.html")


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    """Agent 对话页（兼容旧链接）"""
    return templates.TemplateResponse(request=request, name="chat.html")


@router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request):
    """独立工具页"""
    return templates.TemplateResponse(request=request, name="tools.html")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """设置页"""
    return templates.TemplateResponse(request=request, name="settings.html")


@router.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok", "version": "0.3.0"}
