"""淬火炉保温段判定核心逻辑。

两种判定模式：

- ``strict``（默认）：有效保温段由**连续记录**构成——每条温度都落在闭区间
  [TEMP_LOW, TEMP_HIGH]，且任意相邻记录时间差不超过 MAX_GAP_SECONDS；一次越界
  或超间隔立即切段。段持续时间 = 末项 t − 首项 t，达到 MIN_SOAK_SECONDS 即合格。
- ``linear_equivalent``（线性曲线等效保温）：把 60 秒内的相邻记录点连成线段，
  裁剪出温度落在 [TEMP_LOW, TEMP_HIGH] 的时间片，对片内权重
  ``2**((温度-850)/10)`` 按时间积分，累计等效秒达到 MIN_SOAK_SECONDS 即合格。
  带外区间或相邻点间隔超过 60 秒都结束当前连续段；落在同一温度边界的相邻片
  按同一时刻拼接、不重复计时。首次达标时刻由指数积分的解析反函数求出。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

TEMP_LOW = 840.0
TEMP_HIGH = 860.0
MAX_GAP_SECONDS = 60
MIN_SOAK_SECONDS = 1800

# 线性等效模式的权重：2**((温度-850)/10)
WEIGHT_REF_TEMP = 850.0
WEIGHT_TEMP_SPAN = 10.0
WEIGHT_BASE = 2.0

# 判定模式取值（analysis_mode 表单字段）；缺省与任何旧数据都按严格判定
MODE_STRICT = "strict"
MODE_LINEAR_EQUIVALENT = "linear_equivalent"
ANALYSIS_MODES = (MODE_STRICT, MODE_LINEAR_EQUIVALENT)

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


def analyze(records: list[Record], mode: str = MODE_STRICT) -> dict:
    """返回整份分析结果：是否合格、最早达标段、最长有效段。

    ``mode`` 取 ``strict``（默认）或 ``linear_equivalent``。
    """
    if mode == MODE_LINEAR_EQUIVALENT:
        return analyze_linear_equivalent(records)
    return analyze_strict(records)


def analyze_strict(records: list[Record]) -> dict:
    """严格判定：每条记录温度在带内、相邻间隔 ≤ 60 秒的连续段，时长达标。"""
    infos = [_segment_info(seg) for seg in find_segments(records)]
    earliest = next(
        (info for info in infos if info["duration"] >= MIN_SOAK_SECONDS), None
    )
    longest = max(infos, key=lambda info: info["duration"], default=None)
    return {
        "analysisMode": MODE_STRICT,
        "recordCount": len(records),
        "segmentCount": len(infos),
        "qualified": earliest is not None,
        "earliestQualifyingSegment": earliest,
        "longestSegment": longest,
        "limits": _limits(),
    }


def _limits() -> dict:
    return {
        "tempLow": TEMP_LOW,
        "tempHigh": TEMP_HIGH,
        "maxGapSeconds": MAX_GAP_SECONDS,
        "minSoakSeconds": MIN_SOAK_SECONDS,
    }


# --------------------------------------------------------------------------- #
# 线性曲线等效保温
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Slice:
    """一段温度落在 [840, 860] 的时间片（相邻记录点线段与温区的交集）。

    startT/endT 为整数秒锚点（记录点时间戳），startOffset/endOffset 为相对锚点
    的十进制秒偏移（[0, 60]），合起来即精确的浮点时刻；这样与超大时间戳相加时
    不丢失精度。startW/endW 为片端点的权重 2**((温度-850)/10)。
    """

    start_t: int
    start_offset: float
    end_t: int
    end_offset: float
    start_w: float
    end_w: float

    @property
    def duration(self) -> float:
        return (self.end_t - self.start_t) + (self.end_offset - self.start_offset)


def _weight(temp: float) -> float:
    return WEIGHT_BASE ** ((temp - WEIGHT_REF_TEMP) / WEIGHT_TEMP_SPAN)


def _edge_slice(prev: Record, nxt: Record) -> _Slice | None:
    """相邻两点连成的线段与温区 [TEMP_LOW, TEMP_HIGH] 的交集时间片。

    直线与水平温区的交集至多为一段时间片；无交集返回 None，
    两点间隔超过 MAX_GAP_SECONDS 同样返回 None（由调用方据此切段）。
    落在记录点上的片端点折算为该点整数锚点加 0 偏移，使相邻边在同一点的
    片能用 (锚点, 偏移) 判等拼接；带内边界交点为 prev 锚点加十进制秒偏移。
    """
    gap = nxt.t - prev.t
    if gap > MAX_GAP_SECONDS:
        return None

    t0, t1 = prev.t, nxt.t
    y0, y1 = prev.temp, nxt.temp
    dt = float(gap)

    def make(p_lo: float, p_hi: float) -> _Slice:
        # 以归一化参数 p∈[0,1] 定位：x = p*dt；权重随温度线性变化 ⇒ 指数取值
        x_lo, x_hi = p_lo * dt, p_hi * dt
        if y1 == y0:
            w0 = w1 = _weight(y0)
        else:
            w0 = _weight(y0 + (y1 - y0) * p_lo)
            w1 = _weight(y0 + (y1 - y0) * p_hi)
        # 端点时刻折算成“整数锚点 + 十进制秒偏移”；恰落在记录点上用该点锚点，
        # 使相邻边在同一记录点的片可用 (锚点, 0.0) 精确判等拼接
        if p_lo == 1.0:
            lo_anchor, lo_off = t1, 0.0
        else:
            lo_anchor, lo_off = t0, x_lo  # p_lo == 0.0 → (t0, 0.0)
        if p_hi == 1.0:
            hi_anchor, hi_off = t1, 0.0
        else:
            hi_anchor, hi_off = t0, x_hi
        return _Slice(lo_anchor, lo_off, hi_anchor, hi_off, w0, w1)

    if y0 == y1:
        # 水平线段：在带内则整片保留（恒温片，权重常量）
        return make(0.0, 1.0) if TEMP_LOW <= y0 <= TEMP_HIGH else None

    # 线段到达两条温度边界的归一化位置（恰在端点时不作为内部切割点）
    p_low = (TEMP_LOW - y0) / (y1 - y0)
    p_high = (TEMP_HIGH - y0) / (y1 - y0)
    cuts = sorted(p for p in (p_low, p_high) if 0.0 < p < 1.0)

    bounds = [0.0, *cuts, 1.0]
    for lo, hi in zip(bounds, bounds[1:]):
        # 以子段中点温度判定在带内还是带外（切点不会落在中点）
        mid_temp = y0 + (y1 - y0) * ((lo + hi) / 2.0)
        if TEMP_LOW <= mid_temp <= TEMP_HIGH:
            return make(lo, hi)
    return None


def _slice_integral(sl: _Slice) -> float:
    """片内权重按时间的积分（等效秒）；权重沿时间指数变化。"""
    duration = sl.duration
    if duration <= 0:
        return 0.0
    # 权重从 start_w 指数变化到 end_w（线性温度 ⇒ 指数权重）
    if sl.end_w == sl.start_w:
        return sl.start_w * duration
    return duration * (sl.end_w - sl.start_w) / math.log(sl.end_w / sl.start_w)


def _invert_in_segment(needed: float, sl: _Slice) -> float:
    """在片内求从片起点累计 ``needed`` 等效秒所需的时间长度（解析反函数）。

    权重 W(x) = start_w * exp(k x)，其中 k = ln(end_w/start_w)/duration；
    积分 start_w*(exp(k s)-1)/k = needed，解出 s。
    恒温片（start_w == end_w）按常量积分 s = needed/start_w。
    """
    duration = sl.duration
    if sl.end_w == sl.start_w:
        s = needed / sl.start_w
    else:
        k = math.log(sl.end_w / sl.start_w) / duration
        s = math.log1p(needed * k / sl.start_w) / k
    return min(max(s, 0.0), duration)


def analyze_linear_equivalent(records: list[Record]) -> dict:
    """线性曲线等效保温判定。

    逐对相邻记录点求与温区的交集时间片；间隔超 60 秒或线段整体落在带外导致
    连续片中断时结束当前连续段。落在同一温度边界、同一时刻的相邻片拼接而不
    重复计时。累计等效秒达到 1800 的最早连续段为达标段，并给出首次达标时刻。
    """
    segments: list[list[_Slice]] = []
    current: list[_Slice] = []
    prev_end: tuple[int, float] | None = None  # 上一片结束（锚点, 偏移）

    def close_segment() -> None:
        nonlocal current, prev_end
        if current:
            segments.append(current)
        current = []
        prev_end = None

    for i in range(len(records) - 1):
        sl = _edge_slice(records[i], records[i + 1])
        if sl is None:
            # 超 60 秒间隔，或该线段与温区无交集（带外区间）→ 结束连续段
            close_segment()
            continue
        start_key = (sl.start_t, sl.start_offset)
        if current and start_key != prev_end:
            # 相邻片未在同一时刻相接（中间存在带外时间）→ 切段
            close_segment()
        current.append(sl)
        prev_end = (sl.end_t, sl.end_offset)
    close_segment()

    infos = [_linear_segment_info(seg) for seg in segments]
    earliest = next((info for info in infos if info["qualified"]), None)
    longest = max(infos, key=lambda info: info["equivalentSeconds"], default=None)
    return {
        "analysisMode": MODE_LINEAR_EQUIVALENT,
        "recordCount": len(records),
        "segmentCount": len(infos),
        "qualified": earliest is not None,
        "earliestQualifyingSegment": earliest,
        "longestSegment": longest,
        "limits": _limits(),
    }


def _linear_segment_info(segment: list[_Slice]) -> dict:
    """汇总一个连续段：起止时刻、累计等效秒、最长恒温判定及首次达标时刻。"""
    equivalent = 0.0
    reach_t: int | None = None
    reach_offset: float | None = None
    for sl in segment:
        piece = _slice_integral(sl)
        if reach_t is None and equivalent + piece + 1e-9 >= MIN_SOAK_SECONDS:
            # 首次达标落在本片：用解析反函数求精确时刻
            need = MIN_SOAK_SECONDS - equivalent
            s = _invert_in_segment(need, sl)
            if s >= sl.duration - 1e-9:
                # 恰落在片末记录点：归一为该点整数锚点 + 0 偏移
                reach_t, reach_offset = sl.end_t, sl.end_offset
            else:
                reach_t, reach_offset = sl.start_t, sl.start_offset + s
        equivalent += piece

    first, last = segment[0], segment[-1]
    qualified = reach_t is not None
    info = {
        "startT": first.start_t,
        "startOffset": round(first.start_offset, 6),
        "endT": last.end_t,
        "endOffset": round(last.end_offset, 6),
        "duration": (last.end_t - first.start_t)
        + (last.end_offset - first.start_offset),
        "equivalentSeconds": equivalent,
        "slices": len(segment),
        "qualified": qualified,
    }
    if qualified:
        info["reachT"] = reach_t
        info["reachOffset"] = round(reach_offset, 6)
    return info
