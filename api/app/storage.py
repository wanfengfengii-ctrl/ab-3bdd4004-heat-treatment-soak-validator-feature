"""本地 SQLite 持久化：成功分析的炉次记录。

每次分析成功（校验通过、判定完成）后写入一行：炉次号（可空）、
原文件名、服务端分析时间与完整结论 JSON。重复炉次号允许存在（返工复测）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

RECENT_LIMIT = 20

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "history.db"


class StorageError(Exception):
    """持久化写入失败时抛出：本次分析必须返回明确错误。"""


def connect(db_path: str | os.PathLike[str] = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """打开连接并确保表结构就绪；目录缺失时自动创建。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 连接按请求创建、在用完即关；check_same_thread=False 是为兼容 FastAPI
    # “同步依赖在线程池建连、async 端点在事件循环线程使用”的场景
    conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            heat_no       TEXT,
            filename      TEXT NOT NULL,
            analyzed_at   REAL NOT NULL,
            conclusion    TEXT NOT NULL
        )
        """
    )
    # 按分析时间倒序回看；重复炉次号不做唯一约束（允许返工复测）
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_analyses_analyzed_at "
        "ON analyses (analyzed_at DESC, id DESC)"
    )
    conn.commit()


@contextmanager
def _autocommit(conn: sqlite3.Connection):
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def insert_analysis(
    conn: sqlite3.Connection,
    *,
    heat_no: str | None,
    filename: str,
    conclusion: dict,
    analyzed_at: float | None = None,
) -> int:
    """写入一条成功分析，返回自增记录标识。失败时抛 StorageError。"""
    heat_no = heat_no.strip() if isinstance(heat_no, str) and heat_no.strip() else None
    if analyzed_at is None:
        analyzed_at = time.time()
    try:
        payload = json.dumps(conclusion, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise StorageError(f"结论无法序列化: {exc}") from exc
    try:
        with _autocommit(conn):
            cur = conn.execute(
                "INSERT INTO analyses (heat_no, filename, analyzed_at, conclusion) "
                "VALUES (?, ?, ?, ?)",
                (heat_no, filename, analyzed_at, payload),
            )
        return int(cur.lastrowid)
    except sqlite3.Error as exc:
        raise StorageError(f"历史记录写入失败: {exc}") from exc


def _row_to_summary(row: sqlite3.Row) -> dict:
    conclusion = json.loads(row["conclusion"])
    # 无 analysisMode 字段的旧记录一律按严格判定读取
    mode = conclusion.get("analysisMode") or "strict"
    return {
        "id": row["id"],
        "heatNo": row["heat_no"],
        "filename": row["filename"],
        "analyzedAt": row["analyzed_at"],
        "qualified": bool(conclusion.get("qualified")),
        "recordCount": conclusion.get("recordCount"),
        "analysisMode": mode,
    }


def list_recent(
    conn: sqlite3.Connection, limit: int = RECENT_LIMIT
) -> list[dict]:
    """按分析时间倒序取最近若干条摘要（同时间以 id 倒序兜底）。"""
    rows = conn.execute(
        "SELECT id, heat_no, filename, analyzed_at, conclusion "
        "FROM analyses ORDER BY analyzed_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_summary(row) for row in rows]


def get_by_id(conn: sqlite3.Connection, record_id: int) -> dict | None:
    """按标识取回当时的完整记录（含完整结论）；不存在返回 None。"""
    row = conn.execute(
        "SELECT id, heat_no, filename, analyzed_at, conclusion "
        "FROM analyses WHERE id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        return None
    conclusion = json.loads(row["conclusion"])
    # 无模式的旧记录回看时按严格判定标明
    if not conclusion.get("analysisMode"):
        conclusion["analysisMode"] = "strict"
    return {
        "id": row["id"],
        "heatNo": row["heat_no"],
        "filename": row["filename"],
        "analyzedAt": row["analyzed_at"],
        "conclusion": conclusion,
    }
