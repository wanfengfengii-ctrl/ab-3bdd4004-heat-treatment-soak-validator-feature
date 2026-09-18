"""API 层判据：真实上传链路，合格/不合格/拒绝三种走向。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def upload(records, filename="data.json", analysis_mode=None):
    body = json.dumps(records).encode("utf-8")
    data = {}
    if analysis_mode is not None:
        data["analysis_mode"] = analysis_mode
    return client.post(
        "/api/analyze",
        files={"file": (filename, body, "application/json")},
        data=data,
    )


def qualified_series():
    return [{"t": i * 30, "temp": 850} for i in range(61)]  # 0..1800


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_qualified_upload():
    resp = upload(qualified_series())
    assert resp.status_code == 200
    data = resp.json()
    assert data["qualified"] is True
    assert data["earliestQualifyingSegment"]["startT"] == 0
    assert data["earliestQualifyingSegment"]["endT"] == 1800
    assert data["recordCount"] == 61


def test_unqualified_upload_reports_longest():
    resp = upload([{"t": i * 30, "temp": 850} for i in range(10)])  # 时长 270
    assert resp.status_code == 200
    data = resp.json()
    assert data["qualified"] is False
    assert data["longestSegment"]["duration"] == 270


def test_invalid_payload_rejected_with_code():
    resp = upload([{"t": 1, "temp": 850}, {"t": 1, "temp": 851}])
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "timestamps_not_strictly_increasing"
    assert "message" in detail


def test_missing_field_rejected():
    resp = upload([{"t": 0, "temp": 850}, {"t": 1}])
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "missing_field"


def test_non_finite_rejected():
    body = b'[{"t": 0, "temp": 850}, {"t": 1, "temp": NaN}]'
    resp = client.post(
        "/api/analyze", files={"file": ("data.json", body, "application/json")}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "non_finite_value"


def test_oversize_file_rejected():
    big = b"[" + b",".join(
        b'{"t": %d, "temp": 850}' % i for i in range(200_000)
    ) + b"]"
    assert len(big) > 2 * 1024 * 1024
    resp = client.post(
        "/api/analyze", files={"file": ("big.json", big, "application/json")}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "file_too_large"


def test_missing_file_field_rejected():
    resp = client.post("/api/analyze")
    assert resp.status_code == 422


def test_huge_integer_timestamps_accepted():
    # 超大但合法的整数秒时间戳（超出 JS Date 可表示范围）必须正常判定
    base = 10_000_000_000_000
    resp = upload([{"t": base + i * 30, "temp": 850} for i in range(61)])
    assert resp.status_code == 200
    data = resp.json()
    assert data["qualified"] is True
    seg = data["earliestQualifyingSegment"]
    assert seg["startT"] == base
    assert seg["endT"] == base + 1800
    assert seg["duration"] == 1800


def test_extreme_integer_timestamps_exact_roundtrip():    # 超出 JS 安全整数（2^53）的时间戳：响应必须保留精确数字，
    # 前端依赖响应原文把起止还原成相差 1800 秒的两个不同值
    base = 10**20
    resp = upload([{"t": base + i * 30, "temp": 850} for i in range(61)])
    assert resp.status_code == 200
    seg = resp.json()["earliestQualifyingSegment"]
    assert seg["startT"] == base
    assert seg["endT"] == base + 1800
    assert str(base) in resp.text
    assert str(base + 1800) in resp.text


def test_default_mode_is_strict():
    resp = upload(qualified_series())
    assert resp.json()["analysisMode"] == "strict"


def test_explicit_strict_mode():
    resp = upload(qualified_series(), analysis_mode="strict")
    assert resp.status_code == 200
    assert resp.json()["analysisMode"] == "strict"


def test_linear_equivalent_mode_accepted_and_snapshot():
    # 边界缓慢穿入后恒温达标：线性合格，结论快照记录模式与等效秒
    records = [{"t": 0, "temp": 830}, {"t": 60, "temp": 850}]
    records += [{"t": 90 + i * 30, "temp": 850} for i in range(60)]
    resp = upload(records, analysis_mode="linear_equivalent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["analysisMode"] == "linear_equivalent"
    assert data["qualified"] is True
    seg = data["earliestQualifyingSegment"]
    assert seg["equivalentSeconds"] >= 1800
    # 首次达标时刻为整数锚点 + 十进制秒偏移
    assert isinstance(seg["reachT"], int)
    assert isinstance(seg["reachOffset"], (int, float))


def test_linear_mode_verdict_differs_from_strict():
    # 860 °C 恒温 900 物理秒：严格模式不合格（<1800 秒），等效模式合格（权重 2）
    records = [{"t": i * 30, "temp": 860} for i in range(31)]
    strict = upload(records, analysis_mode="strict").json()
    linear = upload(records, analysis_mode="linear_equivalent").json()
    assert strict["qualified"] is False
    assert linear["qualified"] is True


def test_unknown_mode_rejected_before_parsing():
    # 文件本身也非法（时间戳重复）：未知模式必须先被识别返回，code 可识别
    resp = upload(
        [{"t": 1, "temp": 850}, {"t": 1, "temp": 851}], analysis_mode="bogus"
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "invalid_analysis_mode"
    assert "message" in detail


def test_unknown_mode_with_valid_file_still_rejected():
    resp = upload(qualified_series(), analysis_mode="weird")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_analysis_mode"


def test_unknown_mode_leaves_no_history():
    resp = upload(qualified_series(), analysis_mode="nope")
    assert resp.status_code == 422
    items = client.get("/api/history").json()["items"]
    assert items == []
