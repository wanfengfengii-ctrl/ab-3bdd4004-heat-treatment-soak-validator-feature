"""历史记录持久化判据：成功落库、失败不落库、排序与旧请求兼容。

每个用例使用 tmp_path 下的临时数据库，互不污染。
"""

import json
import sqlite3

from app import storage

# client 夹具（临时 SQLite TestClient）由 conftest.py 提供


def qualified_series(n=61):
    return [{"t": i * 30, "temp": 850} for i in range(n)]  # 0..1800 合格


def unqualified_series():
    return [{"t": i * 30, "temp": 850} for i in range(10)]  # 270 秒，不合格


def upload(client_obj, records, filename="data.json", heat_no=None, mode=None):
    body = json.dumps(records).encode("utf-8")
    files = {"file": (filename, body, "application/json")}
    data = {"heat_no": heat_no} if heat_no is not None else {}
    if mode is not None:
        data["analysis_mode"] = mode
    return client_obj.post("/api/analyze", files=files, data=data)


def test_successful_analysis_is_persisted(client):
    c, db_path = client
    resp = upload(c, qualified_series(), "炉次A.json", heat_no="H-2026-001")
    assert resp.status_code == 200
    data = resp.json()

    # 原响应追加记录标识，原字段保持不变
    assert isinstance(data["historyId"], int)
    assert data["qualified"] is True
    assert data["earliestQualifyingSegment"]["endT"] == 1800

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT heat_no, filename, analyzed_at, conclusion FROM analyses"
        ).fetchone()
    heat_no, filename, analyzed_at, conclusion_raw = row
    assert heat_no == "H-2026-001"
    assert filename == "炉次A.json"
    assert analyzed_at > 0  # 服务端分析时间
    conclusion = json.loads(conclusion_raw)
    assert conclusion["qualified"] is True
    assert conclusion["recordCount"] == 61
    # 落库的是完整结论（含 limits、段信息等）
    assert conclusion["earliestQualifyingSegment"]["startT"] == 0
    assert conclusion["limits"]["minSoakSeconds"] == 1800


def test_unqualified_analysis_is_also_persisted(client):
    c, _ = client
    resp = upload(c, unqualified_series(), heat_no="H-002")
    assert resp.status_code == 200
    items = c.get("/api/history").json()["items"]
    assert len(items) == 1
    assert items[0]["qualified"] is False
    assert items[0]["heatNo"] == "H-002"


def test_validation_failure_leaves_no_history(client):
    c, db_path = client
    bad = [{"t": 0, "temp": 850}, {"t": 0, "temp": 851}]  # 时间戳未严格递增
    resp = upload(c, bad, "bad.json", heat_no="H-BAD")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "timestamps_not_strictly_increasing"

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    assert count == 0
    assert c.get("/api/history").json()["items"] == []


def test_legacy_request_without_heat_no_still_succeeds(client):
    """未传炉次号的旧客户端：请求体里只有 file，仍然成功并落库。"""
    c, _ = client
    body = json.dumps(qualified_series()).encode()
    resp = c.post(
        "/api/analyze",
        files={"file": ("legacy.json", body, "application/json")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["qualified"] is True
    assert "historyId" in data

    items = c.get("/api/history").json()["items"]
    assert len(items) == 1
    assert items[0]["heatNo"] is None
    assert items[0]["filename"] == "legacy.json"


def test_blank_heat_no_normalized_to_null(client):
    c, _ = client
    resp = upload(c, qualified_series(), heat_no="   ")
    assert resp.status_code == 200
    assert c.get("/api/history").json()["items"][0]["heatNo"] is None


def test_duplicate_heat_no_allowed(client):
    """返工复测：同一炉次号允许重复存在。"""
    c, _ = client
    r1 = upload(c, unqualified_series(), "first.json", heat_no="H-DUP")
    r2 = upload(c, qualified_series(), "second.json", heat_no="H-DUP")
    assert r1.status_code == 200
    assert r2.status_code == 200
    items = c.get("/api/history").json()["items"]
    assert [i["heatNo"] for i in items] == ["H-DUP", "H-DUP"]
    # 文件名不同，可区分两次复测
    assert items[0]["filename"] == "second.json"


def test_history_ordered_by_analyzed_at_desc(client):
    c, db_path = client
    first = upload(c, unqualified_series(), "first.json", heat_no="H1").json()
    second = upload(c, qualified_series(), "second.json", heat_no="H2").json()
    # 即便后写入的记录被改成更早的分析时间，列表仍按分析时间倒序
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE analyses SET analyzed_at = ? WHERE id = ?",
            (2000.0, first["historyId"]),
        )
        conn.execute(
            "UPDATE analyses SET analyzed_at = ? WHERE id = ?",
            (1000.0, second["historyId"]),
        )
        conn.commit()
    items = c.get("/api/history").json()["items"]
    assert [i["id"] for i in items] == [first["historyId"], second["historyId"]]
    assert items[0]["analyzedAt"] == 2000.0


def test_history_same_timestamp_falls_back_to_id_desc(client, monkeypatch):
    """分析时间完全相同时，以 id 倒序兜底，保证列表稳定。"""
    c, _ = client
    fixed = 1000.0

    # insert_analysis 默认用 time.time()；这里固定所有 analyzed_at。
    # 必须打补丁到端点解析名字的 app.main 模块
    import app.main as main_module

    real_insert = main_module.insert_analysis

    def frozen_insert(conn, **kwargs):
        kwargs["analyzed_at"] = fixed
        return real_insert(conn, **kwargs)

    monkeypatch.setattr(main_module, "insert_analysis", frozen_insert)
    r1 = upload(c, unqualified_series(), "a.json", heat_no="HA")
    r2 = upload(c, qualified_series(), "b.json", heat_no="HB")
    items = c.get("/api/history").json()["items"]
    assert [i["id"] for i in items] == [
        r2.json()["historyId"],
        r1.json()["historyId"],
    ]


def test_history_limited_to_twenty(client):
    c, _ = client
    for i in range(25):
        resp = upload(c, unqualified_series(), f"f{i}.json", heat_no=f"H{i}")
        assert resp.status_code == 200
    items = c.get("/api/history").json()["items"]
    assert len(items) == 20
    # 最新的 20 条：H5..H24，H0..H4 被截掉
    assert items[0]["heatNo"] == "H24"
    assert items[-1]["heatNo"] == "H5"


def test_history_detail_restores_full_conclusion(client):
    c, _ = client
    resp = upload(c, qualified_series(), "full.json", heat_no="H-FULL")
    body = resp.json()
    history_id = body["historyId"]

    detail = c.get(f"/api/history/{history_id}")
    assert detail.status_code == 200
    item = detail.json()
    assert item["id"] == history_id
    assert item["heatNo"] == "H-FULL"
    assert item["filename"] == "full.json"
    assert "analyzedAt" in item
    # 落库结论 = 响应去掉追加的 historyId
    expected = {k: v for k, v in body.items() if k != "historyId"}
    assert item["conclusion"] == expected
    # 完整结论可再次直接驱动页面唯一结论
    seg = item["conclusion"]["earliestQualifyingSegment"]
    assert (seg["startT"], seg["endT"], seg["duration"]) == (0, 1800, 1800)


def test_history_detail_unknown_id_404(client):
    c, _ = client
    assert c.get("/api/history/99999").status_code == 404


def test_history_summary_shape(client):
    c, _ = client
    upload(c, qualified_series(), "s.json", heat_no="H-S")
    item = c.get("/api/history").json()["items"][0]
    assert set(item.keys()) == {
        "id", "heatNo", "filename", "analyzedAt", "qualified", "recordCount",
        "analysisMode",
    }
    assert item["recordCount"] == 61
    # 默认严格判定，摘要标明判定方式
    assert item["analysisMode"] == "strict"


def test_write_failure_returns_clear_error_and_no_partial_record(client, monkeypatch):
    """持久化写入失败：返回明确错误，库中不留记录，结论不返回。"""
    c, db_path = client

    def boom(*args, **kwargs):
        raise storage.StorageError("模拟磁盘故障")

    # main.py 以 from .storage import insert_analysis 绑定名字，必须打补丁到该模块
    import app.main as main_module

    monkeypatch.setattr(main_module, "insert_analysis", boom)
    resp = upload(c, qualified_series(), "x.json", heat_no="H-X")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["code"] == "history_write_failed"
    assert "模拟磁盘故障" in detail["message"]
    # 不展示未落库结论：错误响应体不含分析结论字段
    assert "qualified" not in resp.json()

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
    assert count == 0


def test_unopenable_database_returns_clear_error(tmp_path, monkeypatch):
    """历史库目录不可用时，分析与查询都返回明确错误而非裸 500。"""
    from fastapi.testclient import TestClient

    import app.main as main_module

    # 父路径是普通文件时无法在其下创建数据库（对 root 同样生效）
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(
        main_module, "DB_PATH", str(blocker / "history.db")
    )
    # conftest 的 autouse 夹具会把 get_conn 重定向到临时库；
    # 本用例要验证 main.get_conn 自身的建连失败处理，需移除覆盖
    main_module.app.dependency_overrides.clear()

    with TestClient(main_module.app, raise_server_exceptions=False) as c:
        body = json.dumps(qualified_series()).encode()
        resp = c.post(
            "/api/analyze",
            files={"file": ("a.json", body, "application/json")},
            data={"heat_no": "H-FAIL"},
        )
        assert resp.status_code == 500
        assert resp.json()["detail"]["code"] == "history_unavailable"
        assert "qualified" not in resp.json()

        hist = c.get("/api/history")
        assert hist.status_code == 500
        assert hist.json()["detail"]["code"] == "history_unavailable"


def test_history_query_failure_returns_500(client, monkeypatch):
    """历史列表查询失败：接口返回 500，供前端只在列表区域提示。"""
    import app.main as main_module

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("模拟查询失败")

    monkeypatch.setattr(main_module, "list_recent", boom)
    c, _ = client
    resp = c.get("/api/history")
    assert resp.status_code == 500


def test_reopen_database_history_survives_new_connection(client):
    """模拟服务重启：重新打开同一数据库文件，记录仍可回看。"""
    c, db_path = client
    resp = upload(c, qualified_series(), "restart.json", heat_no="H-RESTART")
    history_id = resp.json()["historyId"]

    conn = storage.connect(db_path)
    try:
        assert len(storage.list_recent(conn)) == 1
        item = storage.get_by_id(conn, history_id)
        assert item is not None
        assert item["heatNo"] == "H-RESTART"
        assert item["conclusion"]["qualified"] is True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# analysis_mode 历史回看
# ---------------------------------------------------------------------------


def linear_qualified_payload():
    """strict 不合格、linear 合格的低温穿入炉次（与 test_api 同一形状）。"""
    records = [{"t": 0, "temp": 835}, {"t": 60, "temp": 850}]
    t = 90
    while t <= 1830:
        records.append({"t": t, "temp": 850})
        t += 30
    records.append({"t": 1832, "temp": 850})
    return records


def test_linear_upload_persisted_with_mode_and_equivalent_seconds(client):
    c, _ = client
    resp = upload(
        c, linear_qualified_payload(), "linear.json",
        heat_no="H-LIN", mode="linear_equivalent",
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["qualified"] is True
    assert data["analysisMode"] == "linear_equivalent"
    seg = data["earliestQualifyingSegment"]
    assert abs(seg["equivalentSeconds"] - 1800.0) < 1e-9

    # 摘要标明判定方式为线性等效
    item = c.get("/api/history").json()["items"][0]
    assert item["analysisMode"] == "linear_equivalent"
    assert item["qualified"] is True


def test_linear_history_detail_restores_mode_and_anchors(client):
    c, _ = client
    resp = upload(
        c, linear_qualified_payload(), "linear.json",
        heat_no="H-LIN2", mode="linear_equivalent",
    )
    history_id = resp.json()["historyId"]
    detail = c.get(f"/api/history/{history_id}").json()
    conclusion = detail["conclusion"]
    assert conclusion["analysisMode"] == "linear_equivalent"
    seg = conclusion["earliestQualifyingSegment"]
    assert seg["startAnchorT"] == 0
    assert abs(seg["startOffset"] - 20.0) < 0.001
    assert abs(seg["equivalentSeconds"] - 1800.0) < 1e-9


def test_legacy_record_without_mode_read_as_strict(client):
    """无 analysisMode 的旧记录：摘要与详情都按严格判定标明。"""
    c, db_path = client
    # 先按新代码写一条 strict（结论带 analysisMode），再模拟更旧的无模式记录
    upload(c, qualified_series(), "new.json", heat_no="H-NEW")
    legacy_conclusion = {
        "recordCount": 61,
        "segmentCount": 1,
        "qualified": True,
        "earliestQualifyingSegment": {
            "startT": 0, "endT": 1800, "duration": 1800, "points": 61,
        },
        "longestSegment": {
            "startT": 0, "endT": 1800, "duration": 1800, "points": 61,
        },
        "limits": {
            "tempLow": 840.0, "tempHigh": 860.0,
            "maxGapSeconds": 60, "minSoakSeconds": 1800,
        },
    }
    conn = storage.connect(db_path)
    try:
        storage.insert_analysis(
            conn,
            heat_no="H-LEGACY",
            filename="legacy-no-mode.json",
            conclusion=legacy_conclusion,
        )
    finally:
        conn.close()

    items = c.get("/api/history").json()["items"]
    legacy_item = next(i for i in items if i["heatNo"] == "H-LEGACY")
    # 无模式旧记录按严格判定读取
    assert legacy_item["analysisMode"] == "strict"

    detail = c.get(f"/api/history/{legacy_item['id']}").json()
    assert detail["conclusion"]["analysisMode"] == "strict"
    # 旧结论的 strict 段字段保持原样可读
    assert detail["conclusion"]["earliestQualifyingSegment"]["endT"] == 1800
