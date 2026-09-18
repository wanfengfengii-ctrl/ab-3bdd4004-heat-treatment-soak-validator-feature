"""FastAPI 入口：接收 JSON 文件上传，返回保温段分析结论。"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .soak import (
    ANALYSIS_MODES,
    MAX_FILE_BYTES,
    MODE_STRICT,
    Rejection,
    analyze,
    parse_payload,
)
from .storage import (
    DEFAULT_DB_PATH,
    StorageError,
    connect,
    get_by_id,
    insert_analysis,
    list_recent,
)

app = FastAPI(title="淬火炉保温判定 API")

DB_PATH = os.environ.get("HISTORY_DB_PATH", str(DEFAULT_DB_PATH))


def get_conn():
    try:
        conn = connect(DB_PATH)
    except Exception as exc:  # noqa: BLE001 - 卷缺失/只读等，明确报错而非裸 500
        raise HTTPException(
            status_code=500,
            detail={"code": "history_unavailable", "message": f"历史库不可用: {exc}"},
        ) from exc
    try:
        yield conn
    finally:
        conn.close()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze_file(
    file: UploadFile = File(...),
    heat_no: str | None = Form(default=None),
    analysis_mode: str = Form(default=MODE_STRICT),
    conn=Depends(get_conn),
):
    # 未知判定模式先于文件解析拒绝：可识别 code，前端据此清除旧结论
    if analysis_mode not in ANALYSIS_MODES:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "invalid_analysis_mode",
                    "message": (
                        f"未知判定模式 {analysis_mode!r}，"
                        f"仅支持 {', '.join(ANALYSIS_MODES)}"
                    ),
                }
            },
        )

    # 多读 1 字节以识别超限文件，避免无界读取
    raw = await file.read(MAX_FILE_BYTES + 1)
    try:
        records = parse_payload(raw)
    except Rejection as rej:
        # 校验失败：整份拒绝，不留历史
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": rej.code, "message": rej.message}},
        )

    conclusion = analyze(records, mode=analysis_mode)

    # 先落库再返回：持久化失败时本次分析返回明确错误，且不展示未落库结论
    try:
        record_id = insert_analysis(
            conn,
            heat_no=heat_no,
            filename=file.filename or "未命名文件",
            conclusion=conclusion,
        )
    except StorageError as exc:
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "history_write_failed", "message": str(exc)}},
        )

    # 在原响应中追加记录标识，旧字段保持不变（旧客户端可忽略）
    conclusion["historyId"] = record_id
    return conclusion


@app.get("/api/history")
def recent_history(conn=Depends(get_conn)):
    """页面启动时读取按分析时间倒序的最近二十条摘要。"""
    try:
        return {"items": list_recent(conn)}
    except Exception as exc:  # noqa: BLE001 - 历史查询失败只影响列表区域
        raise HTTPException(status_code=500, detail="历史记录读取失败") from exc


@app.get("/api/history/{record_id}")
def history_detail(record_id: int, conn=Depends(get_conn)):
    """恢复一条历史记录当时的唯一结论（完整分析结果）。"""
    item = get_by_id(conn, record_id)
    if item is None:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    return item
