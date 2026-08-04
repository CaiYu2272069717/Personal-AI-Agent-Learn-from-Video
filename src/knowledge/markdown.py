"""Markdown 落盘：library/ 目录镜像"""

from pathlib import Path
from datetime import datetime
from typing import Optional

from ..config import LIBRARY_DIR


def generate_md_content(
    title: str,
    summary_md: str,
    transcript: str = "",
    source: str = "",
    url: str = "",
    author: str = "",
    tags: str = "",
    duration_sec: int = 0,
) -> str:
    """生成完整的 Markdown 文档内容"""
    lines = []
    lines.append(f"# {title}\n")

    # 元信息
    lines.append("| 属性 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 来源 | {source} |")
    if url:
        lines.append(f"| 链接 | [{url}]({url}) |")
    if author:
        lines.append(f"| 作者 | {author} |")
    if duration_sec > 0:
        mins = duration_sec // 60
        secs = duration_sec % 60
        lines.append(f"| 时长 | {mins}分{secs}秒 |")
    if tags:
        lines.append(f"| 标签 | {tags} |")
    lines.append(f"| 收录时间 | {datetime.now().strftime('%Y-%m-%d %H:%M')} |")
    lines.append("")

    # 总结内容
    lines.append("---\n")
    lines.append(summary_md)
    lines.append("")

    # 原始转录（折叠）
    if transcript:
        lines.append("\n---\n")
        lines.append("<details>")
        lines.append("<summary>原始转录</summary>\n")
        lines.append(transcript)
        lines.append("\n</details>")

    return '\n'.join(lines)


def save_markdown(
    title: str,
    summary_md: str,
    transcript: str = "",
    source: str = "",
    url: str = "",
    author: str = "",
    tags: str = "",
    duration_sec: int = 0,
    custom_filename: Optional[str] = None,
) -> Path:
    """保存 Markdown 到 library/ 目录

    命名规则: {日期}-{标题}.md

    Returns:
        保存的文件路径
    """
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    content = generate_md_content(
        title=title,
        summary_md=summary_md,
        transcript=transcript,
        source=source,
        url=url,
        author=author,
        tags=tags,
        duration_sec=duration_sec,
    )

    if custom_filename:
        filename = custom_filename
    else:
        date_str = datetime.now().strftime('%Y%m%d')
        # 清理文件名
        safe_title = title[:50].replace(' ', '_')
        import re
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', safe_title)
        filename = f"{date_str}-{safe_title}.md"

    filepath = LIBRARY_DIR / filename

    # 避免覆盖
    counter = 1
    while filepath.exists():
        stem = filepath.stem
        filepath = LIBRARY_DIR / f"{stem}_{counter}.md"
        counter += 1

    filepath.write_text(content, encoding='utf-8')
    return filepath
