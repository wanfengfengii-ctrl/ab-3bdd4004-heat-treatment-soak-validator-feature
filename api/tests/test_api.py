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
    assert data["analysisMode"] == "strict"
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


def test_extreme_integer_timestamps_exact_roundtrip():
    # 超出 JS 安全整数（2^53）的时间戳：响应必须保留精确数字，
    # 前端依赖响应原文把起止还原成相差 1800 秒的两个不同值
    base = 10**20
    resp = upload([{"t": base + i * 30, "temp": 850} for i in range(61)])
    assert resp.status_code == 200
    seg = resp.json()["earliestQualifyingSegment"]
    assert seg["startT"] == base
    assert seg["endT"] == base + 1800
    assert str(base) in resp.text
    assert str(base + 1800) in resp.text


# ---------------------------------------------------------------------------
# analysis_mode
# ---------------------------------------------------------------------------


def low_temp_penetration_payload():
    """strict 不合格、linear_equivalent 合格的低温穿入炉次。"""
    records = [{"t": 0, "temp": 835}, {"t": 60, "temp": 850}]
    t = 90
    while t <= 1830:
        records.append({"t": t, "temp": 850})
        t += 30
    records.append({"t": 1832, "temp": 850})
    return records


def test_default_request_without_mode_is_strict():
    resp = upload(qualified_series())
    assert resp.status_code == 200
    assert resp.json()["analysisMode"] == "strict"


def test_explicit_strict_mode():
    resp = upload(qualified_series(), analysis_mode="strict")
    assert resp.status_code == 200
    assert resp.json()["analysisMode"] == "strict"


def test_linear_equivalent_mode_qualifies_slow_warmup():
    payload = low_temp_penetration_payload()
    # 同一文件 strict 判不合格
    strict_resp = upload(payload, analysis_mode="strict")
    assert strict_resp.json()["qualified"] is False

    # linear 判合格，快照记录模式与等效秒
    resp = upload(payload, analysis_mode="linear_equivalent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["analysisMode"] == "linear_equivalent"
    assert data["qualified"] is True
    seg = data["earliestQualifyingSegment"]
    assert seg["startAnchorT"] == 0
    assert abs(seg["startOffset"] - 20.0) < 0.001
    assert abs(seg["equivalentSeconds"] - 1800.0) < 1e-9
    # 线性段快照用锚点 + 偏移，不使用 strict 的 startT/duration 字段
    assert "startT" not in seg and "duration" not in seg


def test_unknown_analysis_mode_rejected_422_before_parsing():
    # 未知模式先于文件解析返回 422：即便文件本身也不合法，错误码仍是模式错误
    bad_payload = [{"t": 0, "temp": 850}, {"t": 0, "temp": 851}]
    resp = upload(bad_payload, analysis_mode="equivalent")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "unknown_analysis_mode"
    assert "message" in detail


def test_blank_analysis_mode_defaults_to_strict():
    resp = upload(qualified_series(), analysis_mode="")
    assert resp.status_code == 200
    assert resp.json()["analysisMode"] == "strict"


def test_linear_mode_huge_integer_anchors_preserve_offsets():
    # 线性模式同样要扛超大时间戳：锚点整数精确、偏移为局部十进制秒
    base = 10**20
    payload = [
        {"t": base, "temp": 835},
        {"t": base + 60, "temp": 865},
    ]
    resp = upload(payload, analysis_mode="linear_equivalent")
    assert resp.status_code == 200
    seg = resp.json()["longestSegment"]
    assert seg["startAnchorT"] == base
    assert abs(seg["startOffset"] - 10.0) < 0.001
    assert seg["endAnchorT"] == base
    assert abs(seg["endOffset"] - 50.0) < 0.001
    assert str(base) in resp.text
