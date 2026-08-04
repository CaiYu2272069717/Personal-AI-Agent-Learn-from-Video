"""独立小工具 API 路由

将半自动管线拆解为可独立调用的工具：
- 音频转录：上传音频文件 → 文字
- 视频下载：URL → 本地视频文件
- 文本总结：文本 → 结构化摘要
- OCR：图片 → 文字
- 链接解析：URL → 元数据
- 音频提取：视频 → MP3
"""

import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse

from ..config import get_config, WORKDIR

router = APIRouter(prefix="/tools", tags=["tools"])


# ─── 音频转录工具 ───────────────────────────────────────────

@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
):
    """上传音频文件，直接转录为文字"""
    from ..asr import ASRClient
    from ..media.audio import extract_audio, split_audio

    config = get_config()
    if not config.asr.api_key:
        raise HTTPException(400, "ASR API Key 未配置，请在设置页配置")

    task_id = str(uuid.uuid4())[:8]
    task_dir = WORKDIR / f"tool_transcribe_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)

    # 保存上传文件
    suffix = Path(file.filename or "audio.mp3").suffix or ".mp3"
    input_path = task_dir / f"input{suffix}"
    content = await file.read()
    input_path.write_bytes(content)

    # 如果是视频格式，先提取音频
    audio_path = input_path
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv"}
    if suffix.lower() in video_exts:
        audio_path = task_dir / "audio.mp3"
        await extract_audio(input_path, audio_path)

    # 分段
    segments = await split_audio(audio_path, task_dir / "segments")

    # 转录
    asr = ASRClient()
    full_text = await asr.transcribe_segments(
        [Path(s) for s in segments],
        context=language,
    )

    # 保存结果
    output_path = task_dir / "transcript.txt"
    output_path.write_text(full_text, encoding="utf-8")

    return {
        "task_id": task_id,
        "text": full_text,
        "segments": len(segments),
        "duration_hint": f"{len(content) / 1024:.0f}KB",
    }


# ─── 视频下载工具 ───────────────────────────────────────────

@router.post("/download")
async def download_video_tool(url: str = Form(...)):
    """下载视频到本地"""
    from ..parsers.router import parse_url
    from ..media.downloader import download_video

    # 解析链接
    try:
        meta = await parse_url(url)
    except Exception as e:
        raise HTTPException(400, f"解析失败: {e}")

    if not meta.video_url:
        raise HTTPException(400, "无法解析该链接的视频地址")

    task_id = str(uuid.uuid4())[:8]
    task_dir = WORKDIR / f"tool_download_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)

    # 下载
    title = (meta.title or "video")[:50]
    output_path = await download_video(
        meta.video_url,
        task_dir,
        filename=f"{title}.mp4",
    )

    return {
        "task_id": task_id,
        "title": meta.title,
        "author": meta.author,
        "file_path": str(output_path),
        "file_size": output_path.stat().st_size if output_path.exists() else 0,
    }


@router.get("/download/{task_id}/file")
async def get_downloaded_file(task_id: str):
    """获取已下载的视频文件"""
    task_dir = WORKDIR / f"tool_download_{task_id}"
    if not task_dir.exists():
        raise HTTPException(404, "任务不存在")

    files = list(task_dir.glob("*.mp4")) + list(task_dir.glob("*.mkv"))
    if not files:
        raise HTTPException(404, "文件不存在")

    return FileResponse(str(files[0]), filename=files[0].name)


# ─── 文本总结工具 ───────────────────────────────────────────

@router.post("/summarize")
async def summarize_text(
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    template: str = Form("knowledge"),
):
    """对文本或上传文件进行结构化总结"""
    from ..summary import SummaryClient

    config = get_config()
    if not config.llm.api_key:
        raise HTTPException(400, "总结 LLM API Key 未配置")

    # 获取文本内容
    content = text or ""
    if file:
        raw = await file.read()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("gbk", errors="ignore")

    if not content.strip():
        raise HTTPException(400, "请提供文本内容")

    # 总结
    client = SummaryClient()
    result = await client.summarize(content, template=template)

    return {
        "summary": result,
        "input_length": len(content),
        "template": template,
    }


# ─── OCR 图文识别工具 ──────────────────────────────────────

@router.post("/ocr")
async def ocr_tool(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    """OCR 图片文字识别"""
    from ..ocr import ocr_image

    if not file and not url:
        raise HTTPException(400, "请上传图片或提供图片URL")

    target = url
    if file:
        task_id = str(uuid.uuid4())[:8]
        task_dir = WORKDIR / f"tool_ocr_{task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(file.filename or "img.jpg").suffix or ".jpg"
        img_path = task_dir / f"input{suffix}"
        content = await file.read()
        img_path.write_bytes(content)
        target = str(img_path)

    result = await ocr_image(target)
    return {"text": result, "source": "upload" if file else "url"}


# ─── 链接解析工具 ──────────────────────────────────────────

@router.post("/parse")
async def parse_link(url: str = Form(...)):
    """解析链接元数据"""
    from ..parsers.router import parse_url

    try:
        meta = await parse_url(url)
    except Exception as e:
        raise HTTPException(400, f"无法解析该链接: {e}")

    return {
        "title": meta.title,
        "author": meta.author,
        "platform": meta.source,
        "type": meta.content_type,
        "duration": meta.duration_sec,
        "has_video": bool(meta.video_url),
        "has_images": bool(meta.image_urls),
        "is_multi_part": meta.is_multi_part,
        "parts_count": len(meta.parts),
    }


# ─── 音频提取工具 ──────────────────────────────────────────

@router.post("/extract-audio")
async def extract_audio_tool(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    """从视频中提取音频"""
    from ..media.audio import extract_audio

    task_id = str(uuid.uuid4())[:8]
    task_dir = WORKDIR / f"tool_extract_audio_{task_id}"
    task_dir.mkdir(parents=True, exist_ok=True)

    video_path = None

    if file:
        suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
        video_path = task_dir / f"input{suffix}"
        content = await file.read()
        video_path.write_bytes(content)
    elif url:
        # 先下载视频
        from ..parsers.router import parse_url
        from ..media.downloader import download_video

        try:
            meta = await parse_url(url)
        except Exception as e:
            raise HTTPException(400, f"解析失败: {e}")
        if not meta.video_url:
            raise HTTPException(400, "无法解析视频地址")
        video_path = await download_video(meta.video_url, task_dir, filename="input.mp4")
    else:
        raise HTTPException(400, "请上传视频文件或提供视频URL")

    audio_path = task_dir / "output.mp3"
    await extract_audio(video_path, audio_path)

    return {
        "task_id": task_id,
        "file_path": str(audio_path),
        "file_size": audio_path.stat().st_size if audio_path.exists() else 0,
    }


@router.get("/extract-audio/{task_id}/file")
async def get_extracted_audio(task_id: str):
    """获取已提取的音频文件"""
    task_dir = WORKDIR / f"tool_extract_audio_{task_id}"
    if not task_dir.exists():
        raise HTTPException(404, "任务不存在")

    audio_file = task_dir / "output.mp3"
    if not audio_file.exists():
        raise HTTPException(404, "文件不存在")

    return FileResponse(str(audio_file), filename="audio.mp3")
