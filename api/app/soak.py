"""淬火炉保温段判定核心逻辑。

两种判定方式（analysis_mode）：

- strict（默认，原严格判定）：连续记录中每个温度都落在
  [TEMP_LOW, TEMP_HIGH] 闭区间，且任意相邻记录时间差不超过
  MAX_GAP_SECONDS；一次越界或超间隔立即切段。段持续时间 = 末项 t - 首项 t，
  达到 MIN_SOAK_SECONDS 即合格。
- linear_equivalent（线性曲线等效保温）：把 60 秒以内的相邻采样点连成
  线段，裁剪出温度位于 [TEMP_LOW, TEMP_HIGH] 的时间片，对片内等效权重
  2^((温度-850)/10) 按时间积分；线段越出温区（带外区间）或相邻点间隔
  超过 60 秒即结束连续段，落在边界的相邻片在同一时刻拼接（相接点零长度，
  不重复计时）。连续段累计等效秒达到 MIN_SOAK_SECONDS 即合格。

线性模式的插值时刻一律表示为“整数锚点时间戳 + 十进制秒偏移”，
所有浮点运算只在 0..60 秒的局部偏移上进行，绝不对可能超大的整数
时间戳做浮点加减，避免丢失精度。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

TEMP_LOW = 840.0
TEMP_HIGH = 860.0
TEMP_REF = 850.0
MAX_GAP_SECONDS = 60
MIN_SOAK_SECONDS = 1800

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
MIN_RECORDS = 2
MAX_RECORDS = 10_000

MODE_STRICT = "strict"
MODE_LINEAR = "linear_equivalent"
ANALYSIS_MODES = (MODE_STRICT, MODE_LINEAR)

# 等效权重指数系数：w(T) = 2^((T-850)/10) = exp(LN2/10 * (T-850))
_LN2_OVER_10 = math.log(2.0) / 10.0
_LN2 = math.log(2.0)


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


@dataclass(frozen=True)
class Piece:
    """一条线段落在温区内的时间片，时刻全部使用**线段局部坐标**
    u∈[0, gap]（相对左采样锚点的秒数），避免对超大整数时间戳做浮点运算。

    u0/u1 为片首/片尾相对左锚点的偏移；T0/T1 为对应端点温度；
    left_t/right_t 是该片所属线段的整数采样锚点，只作标识与输出。
    """

    u0: float
    T0: float
    u1: float
    T1: float
    left_t: int
    right_t: int


def normalize_analysis_mode(mode: str | None) -> str:
    """把表单中的 analysis_mode 规范化为内部常量；未知模式直接拒绝。"""
    if mode is None or mode == "":
        return MODE_STRICT
    if mode in ANALYSIS_MODES:
        return mode
    raise Rejection(
        "unknown_analysis_mode",
        f"未知判定方式 {mode!r}：仅支持 strict（严格判定）"
        f"与 linear_equivalent（线性曲线等效保温）",
    )


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


def _limits() -> dict:
    return {
        "tempLow": TEMP_LOW,
        "tempHigh": TEMP_HIGH,
        "maxGapSeconds": MAX_GAP_SECONDS,
        "minSoakSeconds": MIN_SOAK_SECONDS,
    }


# ---------------------------------------------------------------------------
# strict：原严格判定
# ---------------------------------------------------------------------------


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


def _analyze_strict(records: list[Record]) -> dict:
    """原严格判定：采样点全部在带内、间隔不超限才算连续段。"""
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


# ---------------------------------------------------------------------------
# linear_equivalent：线性曲线等效保温
# ---------------------------------------------------------------------------


def _weight(temp: float) -> float:
    """等效保温权重 2^((T-850)/10)。"""
    return math.exp(_LN2_OVER_10 * (temp - TEMP_REF))


def _clip_to_band(
    y0: float, y1: float, gap: float
) -> tuple[float, float, float, float] | None:
    """把局部坐标下的线段（u=0 处温度 y0、u=gap 处 y1）裁剪到温区。

    返回 (u0, T0, u1, T1)：带内片相对左锚点的起止偏移与端点温度（u1 > u0）；
    线段与温区无正长度交集时返回 None。所有浮点运算只在 0..60 的偏移上进行，
    不触碰可能超大的绝对时间戳。
    """
    if y0 == y1:
        # 恒温片
        if TEMP_LOW <= y0 <= TEMP_HIGH:
            return (0.0, y0, gap, y1)
        return None

    # 线段与上下边界的交点局部时刻（升温/降温均适用）
    u_low = (TEMP_LOW - y0) * gap / (y1 - y0)
    u_high = (TEMP_HIGH - y0) * gap / (y1 - y0)
    enter = min(u_low, u_high)
    leave = max(u_low, u_high)
    u0 = max(0.0, enter)
    u1 = min(gap, leave)
    if u1 - u0 <= 1e-12:
        # 仅在边界上相触（零长度）不构成时间片
        return None

    T0 = y0 + (y1 - y0) * u0 / gap
    T1 = y0 + (y1 - y0) * u1 / gap
    # 交点重算可能产生 1e-13 量级越界，夹回温区
    T0 = min(max(T0, TEMP_LOW), TEMP_HIGH)
    T1 = min(max(T1, TEMP_LOW), TEMP_HIGH)
    return (u0, T0, u1, T1)


def _build_linear_segments(records: list[Record]) -> list[list[Piece]]:
    """相邻点连成线段并裁剪带内片，切出连续段。

    连续段在下列任一情况结束：
    - 相邻点间隔超过 60 秒（断档期间温度未知，该边不插值）；
    - 整条线段位于带外（无带内片）；
    - 共享采样点本身越出温区：即使两侧线段各有带内片，两片之间隔着
      带外时间，不得拼接。

    反之，共享采样点落在温区内（含恰在 840/860 边界）时，上一片在该锚点
    结束（u1==gap）、下一片在同一锚点开始（u0==0），按同一时刻拼接；
    相接点零长度，按片积分天然不重复计时。
    """
    segments: list[list[Piece]] = []
    current: list[Piece] = []
    for i, (prev, nxt) in enumerate(zip(records, records[1:])):
        gap = nxt.t - prev.t
        if gap > MAX_GAP_SECONDS:
            # 超限间隔：该边不参与插值并结束连续段
            if current:
                segments.append(current)
                current = []
            continue
        clipped = _clip_to_band(prev.temp, nxt.temp, float(gap))
        if clipped is None:
            # 整条线段在带外（或仅零长度相触）：结束连续段
            if current:
                segments.append(current)
                current = []
            continue
        # 与上一片拼接的前提：共享采样点（prev）在带内，否则两片之间
        # 隔着带外时间，必须先切段
        if current and not (TEMP_LOW <= prev.temp <= TEMP_HIGH):
            segments.append(current)
            current = []
        u0, T0, u1, T1 = clipped
        current.append(
            Piece(u0=u0, T0=T0, u1=u1, T1=T1, left_t=prev.t, right_t=nxt.t)
        )
    if current:
        segments.append(current)
    return segments


def _piece_equivalent(piece: Piece) -> float:
    """单片等效积分 ∫ 2^((T(u)-850)/10) du 的解析值（局部坐标）。

    片内温度线性 T(u)=T0+(T1-T0)(u-u0)/(u1-u0)，记 d=u1-u0、ΔT=T1-T0：
        I = d·10/ln2·w0·(exp(k·ΔT)-1)/ΔT
    恒温片（ΔT=0）退化为 w0·d。用 expm1 避免近恒温时有效数字损失。
    """
    duration = piece.u1 - piece.u0
    w0 = _weight(piece.T0)
    delta = piece.T1 - piece.T0
    if abs(delta) <= 1e-12:
        return w0 * duration
    return duration * 10.0 / _LN2 * w0 * math.expm1(_LN2_OVER_10 * delta) / delta


def _solve_piece_offset(piece: Piece, needed: float) -> float:
    """解析反函数：从片首（u0）再走多少秒，片内累计等效秒恰为 needed。

    片内累计 F(x)=A·(exp(B·x)-1)，其中
        A = d·10/(ln2·ΔT)·w0，B = ln2·ΔT/(10·d)
    反解 x = ln(1 + needed/A) / B；恒温片 x = needed / w0。
    """
    duration = piece.u1 - piece.u0
    w0 = _weight(piece.T0)
    delta = piece.T1 - piece.T0
    if abs(delta) <= 1e-12:
        return min(duration, needed / w0)

    big_a = duration * 10.0 / (_LN2 * delta) * w0
    big_b = _LN2 * delta / (10.0 * duration)
    z = needed / big_a
    # 与片末等效值对齐时浮点可能使 z 略越 exp(Bd)-1，夹住防止 log1p 越界；
    # 升温时 z_end>0、降温时 z_end<0，按大小双向夹取
    z_end = math.expm1(big_b * duration)
    z = min(max(z, min(0.0, z_end)), max(0.0, z_end))
    offset = math.log1p(z) / big_b
    return min(max(offset, 0.0), duration)


def _endpoint(piece: Piece, u: float) -> tuple[int, float]:
    """把片内局部时刻 u（相对该片左锚点）拆成整数锚点 + 十进制秒偏移。

    u 指向线段右端时（u1==gap）把锚点进位到右采样点、偏移归零，
    使落在边界的相邻片首尾报为同一时刻且不重复计时。
    """
    gap = piece.right_t - piece.left_t
    if u <= 1e-9:
        return piece.left_t, 0.0
    if u >= float(gap) - 1e-9:
        return piece.right_t, 0.0
    return piece.left_t, u


def _linear_segment_info(
    pieces: list[Piece],
    equivalent: float,
    hit: tuple[Piece, float] | None = None,
) -> dict:
    """连续段快照。hit=(片, 片内局部达标时刻 u) 时结束点取该时刻，
    否则取段末。"""
    first = pieces[0]
    start_anchor, start_offset = _endpoint(first, first.u0)
    if hit is not None:
        hit_piece, hit_u = hit
        end_anchor, end_offset = _endpoint(hit_piece, hit_u)
    else:
        last = pieces[-1]
        end_anchor, end_offset = _endpoint(last, last.u1)
    # 物理持续秒数：用整数锚点之差（精确）加十进制偏移差，
    # 不对超大绝对时间戳做浮点运算
    physical = (end_anchor - start_anchor) + (end_offset - start_offset)
    return {
        "startAnchorT": start_anchor,
        "startOffset": round(start_offset, 6),
        "endAnchorT": end_anchor,
        "endOffset": round(end_offset, 6),
        "equivalentSeconds": equivalent,
        "physicalSeconds": round(physical, 6),
        "points": len(pieces) + 1,
    }


def _analyze_linear(records: list[Record]) -> dict:
    """线性曲线等效保温判定。"""
    segments = _build_linear_segments(records)

    earliest = None
    longest = None
    longest_equivalent = -1.0

    for pieces in segments:
        piece_totals = [_piece_equivalent(p) for p in pieces]
        full_equivalent = math.fsum(piece_totals)

        # 按时间顺序求该段首次累计达标的片与精确时刻（解析反函数）
        accumulated = 0.0
        hit: tuple[Piece, float] | None = None
        for piece, piece_total in zip(pieces, piece_totals):
            if accumulated + piece_total >= MIN_SOAK_SECONDS - 1e-9:
                needed = min(MIN_SOAK_SECONDS - accumulated, piece_total)
                offset = _solve_piece_offset(piece, needed)
                hit = (piece, piece.u0 + offset)
                break
            accumulated += piece_total

        if hit is not None and earliest is None:
            # 最早达标段：结束点即解析反函数求出的首次达标时刻，
            # 累计等效秒恰为 1800
            earliest = _linear_segment_info(
                pieces, float(MIN_SOAK_SECONDS), hit=hit
            )

        if full_equivalent > longest_equivalent:
            longest_equivalent = full_equivalent
            longest = _linear_segment_info(pieces, full_equivalent)

    return {
        "analysisMode": MODE_LINEAR,
        "recordCount": len(records),
        "segmentCount": len(segments),
        "qualified": earliest is not None,
        "earliestQualifyingSegment": earliest,
        "longestSegment": longest,
        "limits": _limits(),
    }


def analyze(records: list[Record], mode: str = MODE_STRICT) -> dict:
    """返回整份分析结果：是否合格、最早达标段、最长段。"""
    if mode == MODE_LINEAR:
        return _analyze_linear(records)
    return _analyze_strict(records)
