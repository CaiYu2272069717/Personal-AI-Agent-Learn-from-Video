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

-- Agent 后台运行记录。SSE 连接只是观察者，断开不会终止运行。
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    conversation_id INTEGER NOT NULL,
    user_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',  -- queued/running/waiting/completed/failed/cancelled
    error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_conv ON agent_runs(conversation_id, created_at);

-- 可重放的 Agent 过程事件，用于页面切换后恢复思考/工具执行状态。
CREATE TABLE IF NOT EXISTS agent_run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_run_events_run ON agent_run_events(run_id, id);

-- Agent 文件快照（用于 Revert）
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    turn_index INTEGER NOT NULL,  -- 对应触发该轮的 user message id
    file_path TEXT NOT NULL,
    content_before BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

-- Agent Evaluation Golden Cases
CREATE TABLE IF NOT EXISTS eval_cases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    evaluator TEXT NOT NULL,
    expected_json TEXT NOT NULL DEFAULT '{}',
    tags_json TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    builtin INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_eval_cases_category ON eval_cases(category, id);

-- 一次可重复运行的回归评测
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    parent_run_id TEXT,
    label TEXT NOT NULL DEFAULT 'baseline',
    mode TEXT NOT NULL DEFAULT 'offline',
    status TEXT NOT NULL DEFAULT 'queued',
    model TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    total_cases INTEGER NOT NULL DEFAULT 0,
    passed_cases INTEGER NOT NULL DEFAULT 0,
    skipped_cases INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_created ON eval_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_runs_parent ON eval_runs(parent_run_id, label);

-- 每个 Golden Case 的结果与错误归因
CREATE TABLE IF NOT EXISTS eval_case_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    case_name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    passed INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    output_text TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES eval_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES eval_cases(id)
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_case_results(run_id, id);
CREATE INDEX IF NOT EXISTS idx_eval_results_case ON eval_case_results(case_id, created_at DESC);

-- 单步骤模型、工具、守卫与恢复 Trace
CREATE TABLE IF NOT EXISTS eval_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'end',
    duration_ms REAL NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    error_type TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES eval_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_eval_traces_run ON eval_traces(run_id, case_id, id);

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
        # 兼容已经由旧版 schema 创建的数据库。
        cursor = await db.execute("PRAGMA table_info(agent_runs)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "user_message_id" not in columns:
            await db.execute("ALTER TABLE agent_runs ADD COLUMN user_message_id INTEGER")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_runs_message "
            "ON agent_runs(conversation_id, user_message_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_turn "
            "ON snapshots(conversation_id, turn_index, file_path)"
        )
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
