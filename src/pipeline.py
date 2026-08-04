"""任务管线：S1-S7 阶段解耦、全自动/半自动、asyncio 队列、断点续跑

阶段定义:
  S1: 解析链接
  S2: 下载视频
  S3: 提取音频
  S4: 分割音频
  S5: ASR 转录
  S6: LLM 总结
  S7: 入库
"""

import asyncio
import json
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, field
from enum import IntEnum
from datetime import datetime

from .config import get_config, WORKDIR
from .database import Database


class Stage(IntEnum):
    PARSE = 1
    DOWNLOAD = 2
    EXTRACT_AUDIO = 3
    SPLIT_AUDIO = 4
    TRANSCRIBE = 5
    SUMMARIZE = 6
    STORE = 7


STAGE_NAMES = {
    Stage.PARSE: "解析链接",
    Stage.DOWNLOAD: "下载视频",
    Stage.EXTRACT_AUDIO: "提取音频",
    Stage.SPLIT_AUDIO: "分割音频",
    Stage.TRANSCRIBE: "ASR 转录",
    Stage.SUMMARIZE: "LLM 总结",
    Stage.STORE: "入库",
}


@dataclass
class TaskContext:
    """任务运行上下文"""
    task_id: int = 0
    item_id: int = 0
    mode: str = "auto"  # auto / semi
    entry_stage: int = Stage.PARSE
    exit_stage: int = Stage.STORE
    current_stage: int = 0
    work_dir: Path = field(default_factory=lambda: WORKDIR)

    # 中间产物
    input_text: str = ""  # 原始输入 (URL/文本)
    input_file: str = ""  # 本地文件路径
    parse_result: Optional[Any] = None
    video_path: Optional[Path] = None
    audio_path: Optional[Path] = None
    segments: list = field(default_factory=list)
    transcript: str = ""
    summary_md: str = ""
    tags: str = ""
    md_path: str = ""
    template: str = "knowledge"

    # 状态
    status: str = "queued"  # queued/running/completed/failed
    error: str = ""
    progress_callback: Optional[Callable] = None

    @property
    def task_dir(self) -> Path:
        """任务工作目录"""
        d = self.work_dir / str(self.task_id)
        d.mkdir(parents=True, exist_ok=True)
        return d


class Pipeline:
    """处理管线"""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._current_task: Optional[TaskContext] = None

    async def submit(
        self,
        input_text: str = "",
        input_file: str = "",
        mode: str = "auto",
        entry_stage: int = Stage.PARSE,
        exit_stage: int = Stage.STORE,
        template: str = "knowledge",
    ) -> int:
        """提交任务到队列，返回 task_id"""
        # 确定入口阶段
        if input_file:
            path = Path(input_file)
            if path.suffix in ('.mp4', '.mkv', '.avi', '.mov', '.flv'):
                entry_stage = max(entry_stage, Stage.EXTRACT_AUDIO)
            elif path.suffix in ('.mp3', '.wav', '.m4a', '.flac', '.ogg'):
                entry_stage = max(entry_stage, Stage.SPLIT_AUDIO)
            elif path.suffix in ('.txt', '.md'):
                entry_stage = max(entry_stage, Stage.SUMMARIZE)

        # 写入数据库
        async with Database() as db:
            cursor = await db.execute(
                """INSERT INTO tasks (mode, entry_stage, exit_stage, status, input_data)
                   VALUES (?, ?, ?, 'queued', ?)""",
                (mode, entry_stage, exit_stage, json.dumps({
                    "text": input_text, "file": input_file, "template": template
                }))
            )
            await db.commit()
            task_id = cursor.lastrowid

        ctx = TaskContext(
            task_id=task_id,
            mode=mode,
            entry_stage=entry_stage,
            exit_stage=exit_stage,
            input_text=input_text,
            input_file=input_file,
            work_dir=WORKDIR,
            template=template,
        )

        await self._queue.put(ctx)
        return task_id

    async def start_worker(self):
        """启动队列消费者"""
        self._running = True
        while self._running:
            try:
                ctx = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            self._current_task = ctx
            try:
                await self._run_pipeline(ctx)
            except Exception as e:
                ctx.status = "failed"
                ctx.error = str(e)
                await self._update_task_status(ctx)
            finally:
                self._current_task = None
                self._queue.task_done()

    async def stop(self):
        """停止消费者"""
        self._running = False

    async def _run_pipeline(self, ctx: TaskContext):
        """执行管线各阶段"""
        ctx.status = "running"
        await self._update_task_status(ctx)

        stages = [
            (Stage.PARSE, self._stage_parse),
            (Stage.DOWNLOAD, self._stage_download),
            (Stage.EXTRACT_AUDIO, self._stage_extract_audio),
            (Stage.SPLIT_AUDIO, self._stage_split_audio),
            (Stage.TRANSCRIBE, self._stage_transcribe),
            (Stage.SUMMARIZE, self._stage_summarize),
            (Stage.STORE, self._stage_store),
        ]

        for stage_num, stage_func in stages:
            if stage_num < ctx.entry_stage:
                continue
            if stage_num > ctx.exit_stage:
                break

            # 断点续跑：检查已有产物
            if self._has_stage_output(ctx, stage_num):
                self._load_stage_output(ctx, stage_num)
                continue

            ctx.current_stage = stage_num
            await self._update_task_status(ctx)

            if ctx.progress_callback:
                ctx.progress_callback(stage_num, STAGE_NAMES[stage_num])

            await stage_func(ctx)

        ctx.status = "completed"
        await self._update_task_status(ctx)

    # === 各阶段实现 ===

    async def _stage_parse(self, ctx: TaskContext):
        """S1: 解析链接"""
        from .parsers import parse_url
        result = await parse_url(ctx.input_text)
        ctx.parse_result = result

        # 落盘
        import dataclasses
        output = json.dumps(dataclasses.asdict(result), ensure_ascii=False, default=str)
        (ctx.task_dir / "parse_result.json").write_text(output, encoding='utf-8')

    async def _stage_download(self, ctx: TaskContext):
        """S2: 下载视频"""
        from .media.downloader import download_video

        if ctx.parse_result and ctx.parse_result.video_url:
            video_path = await download_video(
                ctx.parse_result.video_url,
                ctx.task_dir,
                f"{ctx.parse_result.source_id}.mp4",
            )
            ctx.video_path = video_path
        elif ctx.input_file and Path(ctx.input_file).suffix in ('.mp4', '.mkv', '.avi'):
            ctx.video_path = Path(ctx.input_file)
        else:
            # 文章类：无需下载，直接用 text_content
            if ctx.parse_result and ctx.parse_result.text_content:
                ctx.transcript = ctx.parse_result.text_content
                # 跳过后续音频阶段
                ctx.entry_stage = Stage.SUMMARIZE

    async def _stage_extract_audio(self, ctx: TaskContext):
        """S3: 提取音频"""
        if not ctx.video_path:
            if ctx.input_file and Path(ctx.input_file).exists():
                ctx.video_path = Path(ctx.input_file)
            else:
                return

        from .media.audio import extract_audio, compress_audio
        audio_path = await extract_audio(ctx.video_path, ctx.task_dir / "audio.mp3")
        # 压缩
        compressed = await compress_audio(audio_path, ctx.task_dir / "audio_16k.mp3")
        ctx.audio_path = compressed

    async def _stage_split_audio(self, ctx: TaskContext):
        """S4: 分割音频"""
        if not ctx.audio_path:
            if ctx.input_file:
                ctx.audio_path = Path(ctx.input_file)
            else:
                return

        from .media.audio import split_audio
        segments = await split_audio(ctx.audio_path, ctx.task_dir / "segments")
        ctx.segments = segments

    async def _stage_transcribe(self, ctx: TaskContext):
        """S5: ASR 转录"""
        if ctx.transcript:  # 文章类已有文本
            (ctx.task_dir / "transcript.txt").write_text(ctx.transcript, encoding='utf-8')
            return

        if not ctx.segments:
            return

        from .asr import ASRClient
        client = ASRClient()
        title_context = ""
        if ctx.parse_result:
            title_context = f"标题: {ctx.parse_result.title}"

        transcript = await client.transcribe_segments(
            ctx.segments,
            context=title_context,
        )
        ctx.transcript = transcript
        (ctx.task_dir / "transcript.txt").write_text(transcript, encoding='utf-8')

    async def _stage_summarize(self, ctx: TaskContext):
        """S6: LLM 总结"""
        if not ctx.transcript:
            # 尝试从文件加载
            txt_file = ctx.task_dir / "transcript.txt"
            if txt_file.exists():
                ctx.transcript = txt_file.read_text(encoding='utf-8')
            elif ctx.input_file and Path(ctx.input_file).suffix in ('.txt', '.md'):
                ctx.transcript = Path(ctx.input_file).read_text(encoding='utf-8')
            else:
                return

        from .summary import SummaryClient
        client = SummaryClient()
        title = ctx.parse_result.title if ctx.parse_result else ""

        ctx.summary_md = await client.summarize(
            ctx.transcript, title=title, template=ctx.template
        )

        # 自动标签
        tags = await client.generate_tags(ctx.summary_md)
        ctx.tags = ','.join(tags)

        (ctx.task_dir / "summary.md").write_text(ctx.summary_md, encoding='utf-8')

    async def _stage_store(self, ctx: TaskContext):
        """S7: 入库"""
        from .knowledge.db import KnowledgeDB
        from .knowledge.markdown import save_markdown

        pr = ctx.parse_result
        title = pr.title if pr else Path(ctx.input_file).stem if ctx.input_file else "untitled"
        source = pr.source if pr else "local"
        source_id = pr.source_id if pr else ctx.input_file or str(ctx.task_id)
        url = pr.url if pr else ""
        author = pr.author if pr else ""
        duration = pr.duration_sec if pr else 0

        # 保存 Markdown
        md_path = save_markdown(
            title=title,
            summary_md=ctx.summary_md,
            transcript=ctx.transcript,
            source=source,
            url=url,
            author=author,
            tags=ctx.tags,
            duration_sec=duration,
        )
        ctx.md_path = str(md_path)

        # 入数据库
        kb = KnowledgeDB()

        # 去重检测
        existing = await kb.get_item_by_source_id(source_id)
        if existing:
            # 更新
            await kb.update_item(
                existing['id'],
                transcript=ctx.transcript,
                summary_md=ctx.summary_md,
                tags=ctx.tags,
                md_path=ctx.md_path,
                status="completed",
            )
            ctx.item_id = existing['id']
        else:
            ctx.item_id = await kb.create_item(
                source=source,
                source_id=source_id,
                title=title,
                url=url,
                author=author,
                duration_sec=duration,
                transcript=ctx.transcript,
                summary_md=ctx.summary_md,
                tags=ctx.tags,
                md_path=ctx.md_path,
                status="completed",
            )

        # 嵌入（异步，不阻塞）
        try:
            from .knowledge.embeddings import EmbeddingClient
            emb_client = EmbeddingClient()
            text_to_embed = f"{title}\n{ctx.summary_md}\n{ctx.transcript[:3000]}"
            await emb_client.embed_and_store(ctx.item_id, text_to_embed)
        except Exception:
            pass  # 嵌入失败不影响主流程

        # 更新 task 表
        async with Database() as db:
            await db.execute(
                "UPDATE tasks SET item_id = ?, status = 'completed' WHERE id = ?",
                (ctx.item_id, ctx.task_id)
            )
            await db.commit()

    # === 辅助方法 ===

    def _has_stage_output(self, ctx: TaskContext, stage: int) -> bool:
        """检查阶段是否已有产物（断点续跑）"""
        td = ctx.task_dir
        checks = {
            Stage.PARSE: td / "parse_result.json",
            Stage.DOWNLOAD: td / f"{ctx.parse_result.source_id if ctx.parse_result else ''}.mp4",
            Stage.EXTRACT_AUDIO: td / "audio_16k.mp3",
            Stage.SPLIT_AUDIO: td / "segments",
            Stage.TRANSCRIBE: td / "transcript.txt",
            Stage.SUMMARIZE: td / "summary.md",
        }
        target = checks.get(stage)
        if target and target.exists():
            return True
        return False

    def _load_stage_output(self, ctx: TaskContext, stage: int):
        """从已有产物恢复上下文"""
        td = ctx.task_dir
        if stage == Stage.PARSE:
            data = json.loads((td / "parse_result.json").read_text(encoding='utf-8'))
            from .parsers.router import ParseResult
            ctx.parse_result = ParseResult(**{k: v for k, v in data.items() if k != 'parts' and k != 'image_urls'})
        elif stage == Stage.EXTRACT_AUDIO:
            ctx.audio_path = td / "audio_16k.mp3"
        elif stage == Stage.SPLIT_AUDIO:
            seg_dir = td / "segments"
            if seg_dir.exists():
                ctx.segments = sorted(seg_dir.glob("*.mp3"))
        elif stage == Stage.TRANSCRIBE:
            ctx.transcript = (td / "transcript.txt").read_text(encoding='utf-8')
        elif stage == Stage.SUMMARIZE:
            ctx.summary_md = (td / "summary.md").read_text(encoding='utf-8')

    async def _update_task_status(self, ctx: TaskContext):
        """更新任务状态到数据库"""
        async with Database() as db:
            await db.execute(
                """UPDATE tasks SET status = ?, current_stage = ?, error = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (ctx.status, ctx.current_stage, ctx.error, ctx.task_id)
            )
            await db.commit()


# 全局单例
_pipeline: Optional[Pipeline] = None


def get_pipeline() -> Pipeline:
    """获取全局 Pipeline 实例"""
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline
