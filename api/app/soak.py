"""淬火炉保温段判定核心逻辑。

有效保温段：连续记录中每个温度都落在 [TEMP_LOW, TEMP_HIGH] 闭区间，
且任意相邻记录时间差不超过 MAX_GAP_SECONDS；一次越界或超间隔立即切段。
段持续时间 = 末项 t - 首项 t，达到 MIN_SOAK_SECONDS 即合格。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

TEMP_LOW = 840.0
TEMP_HIGH = 860.0
MAX_GAP_SECONDS = 60
MIN_SOAK_SECONDS = 1800

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
MIN_RECORDS = 2
MAX_RECORDS = 10_000


class Rejection(Exception):
    """整份文件被拒绝时抛出，携带机器可读 code 与人类可读 message。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Record:
    t: int
    temp: float


def _reject_constant(token: str) -> None:
    # JSON 中的 NaN / Infinity / -Infinity 字面量一律拒绝
    raise Rejection("non_finite_value", f"JSON 含非有限数值字面量: {token}")


def parse_payload(raw: bytes) -> list[Record]:
    """解析并校验上传内容，任何违规都整份拒绝（抛 Rejection）。"""
    if len(raw) == 0:
        raise Rejection("empty_file", "文件为空")
    if len(raw) > MAX_FILE_BYTES:
        raise Rejection(
            "file_too_large",
            f"文件大小 {len(raw)} 字节超过上限 {MAX_FILE_BYTES} 字节 (2 MiB)",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Rejection("invalid_encoding", "文件不是有效的 UTF-8 文本") from exc
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except Rejection:
        raise
    except json.JSONDecodeError as exc:
        raise Rejection("invalid_json", f"JSON 解析失败: {exc.msg} (行 {exc.lineno})") from exc

    if not isinstance(data, list):
        raise Rejection("root_not_array", "JSON 根节点必须是数组")
    if not MIN_RECORDS <= len(data) <= MAX_RECORDS:
        raise Rejection(
            "record_count_out_of_range",
            f"记录数 {len(data)} 不在允许范围 {MIN_RECORDS}~{MAX_RECORDS}",
        )

    records: list[Record] = []
    prev_t: int | None = None
    for i, item in enumerate(data):
        where = f"第 {i} 条记录"
        if not isinstance(item, dict):
            raise Rejection("record_not_object", f"{where}不是对象")
        if "t" not in item:
            raise Rejection("missing_field", f"{where}缺少字段 t")
        if "temp" not in item:
            raise Rejection("missing_field", f"{where}缺少字段 temp")
        t = item["t"]
        temp = item["temp"]
        # bool 是 int 的子类，必须显式排除
        if isinstance(t, bool) or not isinstance(t, int):
            raise Rejection("invalid_t", f"{where}的 t 必须是整数秒时间戳")
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            raise Rejection("invalid_temp", f"{where}的 temp 必须是数值")
        temp = float(temp)
        if not math.isfinite(temp):
            raise Rejection("non_finite_temp", f"{where}的 temp 不是有限数值")
        if prev_t is not None and t <= prev_t:
            raise Rejection(
                "timestamps_not_strictly_increasing",
                f"{where}的时间戳 {t} 未严格大于前一条 {prev_t}",
            )
        prev_t = t
        records.append(Record(t=t, temp=temp))
    return records


def find_segments(records: list[Record]) -> list[list[Record]]:
    """按温度区间与相邻间隔切分有效保温段，段按时间顺序返回。"""
    segments: list[list[Record]] = []
    current: list[Record] = []
    for rec in records:
        if not (TEMP_LOW <= rec.temp <= TEMP_HIGH):
            if current:
                segments.append(current)
                current = []
            continue
        if current and rec.t - current[-1].t > MAX_GAP_SECONDS:
            segments.append(current)
            current = []
        current.append(rec)
    if current:
        segments.append(current)
    return segments


def _segment_info(segment: list[Record]) -> dict:
    start, end = segment[0].t, segment[-1].t
    return {
        "startT": start,
        "endT": end,
        "duration": end - start,
        "points": len(segment),
    }


def analyze(records: list[Record]) -> dict:
    """返回整份分析结果：是否合格、最早达标段、最长有效段。"""
    infos = [_segment_info(seg) for seg in find_segments(records)]
    earliest = next(
        (info for info in infos if info["duration"] >= MIN_SOAK_SECONDS), None
    )
    longest = max(infos, key=lambda info: info["duration"], default=None)
    return {
        "recordCount": len(records),
        "segmentCount": len(infos),
        "qualified": earliest is not None,
        "earliestQualifyingSegment": earliest,
        "longestSegment": longest,
        "limits": {
            "tempLow": TEMP_LOW,
            "tempHigh": TEMP_HIGH,
            "maxGapSeconds": MAX_GAP_SECONDS,
            "minSoakSeconds": MIN_SOAK_SECONDS,
        },
    }
