"""数据库初始化与基础操作：SQLite + FTS5 + sqlite-vec"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Optional

import aiosqlite

from .config import DATA_DIR

DB_PATH = DATA_DIR / "learn_from_video.db"

# === Schema ===

SCHEMA_SQL = """
-- 知识条目主表
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'unknown',  -- douyin / bilibili / article / local
    source_id TEXT UNIQUE,
    url TEXT,
    title TEXT NOT NULL DEFAULT '',
    author TEXT DEFAULT '',
    duration_sec INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/completed/failed
    error TEXT DEFAULT '',
    transcript TEXT DEFAULT '',
    summary_md TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    md_path TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- FTS5 全文检索
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, transcript, summary_md, tags,
    content='items',
    content_rowid='id'
);

-- FTS5 触发器：保持同步
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, transcript, summary_md, tags)
    VALUES (new.id, new.title, new.transcript, new.summary_md, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, transcript, summary_md, tags)
    VALUES ('delete', old.id, old.title, old.transcript, old.summary_md, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, transcript, summary_md, tags)
    VALUES ('delete', old.id, old.title, old.transcript, old.summary_md, old.tags);
    INSERT INTO items_fts(rowid, title, transcript, summary_md, tags)
    VALUES (new.id, new.title, new.transcript, new.summary_md, new.tags);
END;

-- 向量分块表（RAG）
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT '',
    embedding BLOB,
    FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks(item_id);

-- Agent 会话
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agent 消息
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',  -- user/assistant/system/tool
    content TEXT DEFAULT '',
    tool_calls_json TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

-- Agent 文件快照（用于 Revert）
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    turn_index INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    content_before BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- 任务队列
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    mode TEXT NOT NULL DEFAULT 'auto',  -- auto / semi
    entry_stage INTEGER NOT NULL DEFAULT 1,
    exit_stage INTEGER NOT NULL DEFAULT 7,
    current_stage INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',  -- queued/running/completed/failed/cancelled
    error TEXT DEFAULT '',
    input_data TEXT DEFAULT '',  -- JSON: url / file_path etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (item_id) REFERENCES items(id)
);
"""


async def init_db() -> None:
    """初始化数据库：创建目录、执行 schema"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接（调用方负责关闭）"""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


class Database:
    """数据库上下文管理器"""

    def __init__(self):
        self._db: Optional[aiosqlite.Connection] = None

    async def __aenter__(self) -> aiosqlite.Connection:
        self._db = await get_db()
        return self._db

    async def __aexit__(self, *args):
        if self._db:
            await self._db.close()
