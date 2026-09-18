"""线性曲线等效保温（linear_equivalent）判据。

验收要点：
- 低温穿入后达标：strict 判不合格的同一炉次，linear 累计等效秒达到 1800；
- 单线段穿越双边界（835→865°C）的解析积分值；
- 60 秒与超 60 秒间隔的切段差异；
- 结论快照记录模式与等效秒；
- 积分值与首次达标偏移相对高精度数值积分的误差不超过 0.001 秒。
"""

import math

from app.soak import (
    MAX_GAP_SECONDS,
    MIN_SOAK_SECONDS,
    MODE_LINEAR,
    MODE_STRICT,
    Piece,
    Record,
    _build_linear_segments,
    _clip_to_band,
    _piece_equivalent,
    _solve_piece_offset,
    analyze,
    normalize_analysis_mode,
)

LN2 = math.log(2.0)
K = LN2 / 10.0
TOL = 0.001


def weight(temp: float) -> float:
    return 2.0 ** ((temp - 850.0) / 10.0)


def const_series(start_t, count, step, temp):
    return [Record(t=start_t + i * step, temp=temp) for i in range(count)]


# ---------------------------------------------------------------------------
# 独立的高精度数值积分（不依赖被测的解析公式），用于交叉校验
# ---------------------------------------------------------------------------


def numeric_piece_integral(u0, T0, u1, T1, n=20_000):
    """对片内 2^((T-850)/10) 做高密度梯形积分。"""
    d = u1 - u0
    h = d / n
    total = 0.5 * (weight(T0) + weight(T1))
    for i in range(1, n):
        temp = T0 + (T1 - T0) * (i * h) / d
        total += weight(temp)
    return total * h


def numeric_edge_in_band(y0, y1, gap, n=120_000):
    """测试自用的独立裁剪 + 数值积分：一条线段在带内的等效秒。"""
    if y0 == y1:
        return float(gap) * weight(y0) if 840.0 <= y0 <= 860.0 else 0.0
    roots = [
        0.0,
        float(gap),
        (840.0 - y0) * gap / (y1 - y0),
        (860.0 - y0) * gap / (y1 - y0),
    ]
    roots = sorted(u for u in roots if -1e-9 <= u <= gap + 1e-9)
    total = 0.0
    for lo, hi in zip(roots, roots[1:]):
        mid = (lo + hi) / 2.0
        tmid = y0 + (y1 - y0) * mid / gap
        if 840.0 <= tmid <= 860.0 and hi - lo > 1e-12:
            t_lo = min(max(y0 + (y1 - y0) * lo / gap, 840.0), 860.0)
            t_hi = min(max(y0 + (y1 - y0) * hi / gap, 840.0), 860.0)
            total += numeric_piece_integral(lo, t_lo, hi, t_hi, n=4000)
    return total


def temp_at(records, time):
    """分段线性温度曲线在任意时刻的值（仅用于数值校验）。"""
    for prev, nxt in zip(records, records[1:]):
        if prev.t <= time <= nxt.t:
            return prev.temp + (nxt.temp - prev.temp) * (
                time - prev.t
            ) / (nxt.t - prev.t)
    return None


def numeric_integral_range(records, start, end, n=2_000_000):
    """[start, end] 上只对带内温度累计等效秒（数值梯形积分）。"""
    h = (end - start) / n

    def masked(time):
        temp = temp_at(records, time)
        return weight(temp) if 840.0 <= temp <= 860.0 else 0.0

    total = 0.5 * (masked(start) + masked(end))
    for i in range(1, n):
        total += masked(start + i * h)
    return total * h


# ---------------------------------------------------------------------------
# 裁剪几何
# ---------------------------------------------------------------------------


def test_single_edge_crossing_both_boundaries_clip():
    # 60 秒内 835→865：u=10 处穿入 840，u=50 处穿出 860
    assert _clip_to_band(835.0, 865.0, 60.0) == (10.0, 840.0, 50.0, 860.0)


def test_single_edge_cooling_crossing_both_boundaries_clip():
    u0, T0, u1, T1 = _clip_to_band(865.0, 835.0, 60.0)
    assert (u0, u1) == (10.0, 50.0)
    assert (T0, T1) == (860.0, 840.0)


def test_entirely_out_of_band_edge_has_no_piece():
    assert _clip_to_band(800.0, 830.0, 60.0) is None
    assert _clip_to_band(870.0, 900.0, 60.0) is None


def test_edge_only_touching_boundary_has_no_piece():
    # 839→840 全程温度 <840，只在终点 u=60 触及边界：零长度相触 → 无片
    assert _clip_to_band(839.0, 840.0, 60.0) is None
    # 860→861 从上边界升温离开温区，仅起点零长度相触 → 无片
    assert _clip_to_band(860.0, 861.0, 60.0) is None
    # 830→840 同理，只在末点恰好到达下边界 → 无片
    assert _clip_to_band(830.0, 840.0, 60.0) is None


# ---------------------------------------------------------------------------
# 解析积分值
# ---------------------------------------------------------------------------


def test_cross_both_boundaries_integral_closed_form():
    # 片 [u=10,T=840]→[u=50,T=860]，d=40、w0=0.5、ΔT=20：
    # I = 40·10/ln2·0.5·(2^2-1)/20 = 30/ln2
    piece = Piece(u0=10.0, T0=840.0, u1=50.0, T1=860.0, left_t=0, right_t=60)
    expected = 30.0 / LN2
    assert abs(_piece_equivalent(piece) - expected) < 1e-12
    # 与高精度数值积分一致
    numeric = numeric_piece_integral(10.0, 840.0, 50.0, 860.0)
    assert abs(_piece_equivalent(piece) - numeric) < TOL


def test_constant_temperature_piece_uses_constant_integral():
    # 恒温片按常量积分：860°C 权重 2，60 秒 = 120 等效秒
    piece = Piece(u0=0.0, T0=860.0, u1=60.0, T1=860.0, left_t=0, right_t=60)
    assert abs(_piece_equivalent(piece) - 120.0) < 1e-12
    # 840°C 权重 0.5
    piece_low = Piece(u0=0.0, T0=840.0, u1=60.0, T1=840.0, left_t=0, right_t=60)
    assert abs(_piece_equivalent(piece_low) - 30.0) < 1e-12


def test_integral_matches_numeric_across_temperatures():
    # 宽范围随机线段，解析积分必须与数值积分一致
    for y0 in (832.0, 835.0, 840.0, 847.3, 850.0, 858.0, 860.0, 865.0):
        for y1 in (832.0, 836.5, 840.0, 844.0, 850.0, 855.7, 860.0, 868.0):
            clipped = _clip_to_band(y0, y1, 60.0)
            numeric = numeric_edge_in_band(y0, y1, 60.0)
            if clipped is None:
                assert numeric < 1e-6
                continue
            u0, T0, u1, T1 = clipped
            piece = Piece(u0=u0, T0=T0, u1=u1, T1=T1, left_t=0, right_t=60)
            assert abs(_piece_equivalent(piece) - numeric) < TOL


# ---------------------------------------------------------------------------
# 解析反函数：首次达标偏移
# ---------------------------------------------------------------------------


def test_inverse_offset_reconstructs_target_integral():
    piece = Piece(u0=10.0, T0=840.0, u1=50.0, T1=860.0, left_t=0, right_t=60)
    total = _piece_equivalent(piece)
    for frac in (0.05, 0.33, 0.5, 0.78, 0.999):
        needed = total * frac
        x = _solve_piece_offset(piece, needed)
        # 从片首走 x 秒后的数值积分应恰为 needed
        reached = numeric_piece_integral(
            piece.u0, piece.T0, piece.u0 + x,
            piece.T0 + (piece.T1 - piece.T0) * x / (piece.u1 - piece.u0),
        )
        assert abs(reached - needed) < TOL


def test_inverse_offset_constant_piece():
    piece = Piece(u0=0.0, T0=860.0, u1=60.0, T1=860.0, left_t=0, right_t=60)
    # 需要 30 等效秒、权重 2 → 15 物理秒
    assert abs(_solve_piece_offset(piece, 30.0) - 15.0) < 1e-12


def test_inverse_offset_at_piece_end():
    piece = Piece(u0=10.0, T0=840.0, u1=50.0, T1=860.0, left_t=0, right_t=60)
    total = _piece_equivalent(piece)
    x = _solve_piece_offset(piece, total)
    assert abs(x - 40.0) < 1e-9


# ---------------------------------------------------------------------------
# 60 秒与超 60 秒间隔
# ---------------------------------------------------------------------------


def test_gap_of_exactly_60_seconds_keeps_linear_segment():
    records = const_series(0, 3, 60, 850.0)  # t = 0,60,120
    result = analyze(records, MODE_LINEAR)
    assert result["segmentCount"] == 1
    seg = result["longestSegment"]
    assert (seg["startAnchorT"], seg["endAnchorT"]) == (0, 120)
    assert abs(seg["equivalentSeconds"] - 120.0) < TOL


def test_gap_over_60_seconds_cuts_linear_segment():
    # 第一段 0..60（两条 30 秒边拼成 60 等效秒），断档 140 秒，
    # 第二段 200..290（三条 30 秒边拼成 90 等效秒）
    records = [
        Record(0, 850.0), Record(30, 850.0), Record(60, 850.0),
        Record(200, 850.0), Record(230, 850.0),
        Record(260, 850.0), Record(290, 850.0),
    ]
    result = analyze(records, MODE_LINEAR)
    assert result["segmentCount"] == 2
    longest = result["longestSegment"]
    # 断档边不插值；最长为断档后的第二段，从整数锚点 200 起、290 止
    assert abs(longest["equivalentSeconds"] - 90.0) < TOL
    assert longest["startAnchorT"] == 200 and longest["startOffset"] == 0.0
    assert longest["endAnchorT"] == 290 and longest["endOffset"] == 0.0


def test_out_of_band_shared_sample_cuts_segment():
    # 845→835→845：中间采样点带外，两侧带内片隔着带外时间，必须切段
    records = [Record(0, 845.0), Record(30, 835.0), Record(60, 845.0)]
    result = analyze(records, MODE_LINEAR)
    assert result["segmentCount"] == 2


def test_pieces_splice_at_in_band_sample_without_double_count():
    # 850→840→850：共享采样点恰在闭区间边界，两片拼成一段，相接点不重复计时
    records = [Record(0, 850.0), Record(30, 840.0), Record(60, 850.0)]
    result = analyze(records, MODE_LINEAR)
    assert result["segmentCount"] == 1
    seg = result["longestSegment"]
    assert (seg["startAnchorT"], seg["endAnchorT"]) == (0, 60)
    expected = numeric_edge_in_band(850.0, 840.0, 30) + numeric_edge_in_band(
        840.0, 850.0, 30
    )
    assert abs(seg["equivalentSeconds"] - expected) < TOL


# ---------------------------------------------------------------------------
# 端到端判据
# ---------------------------------------------------------------------------


def low_temp_penetration_records():
    """低温穿入炉次：

    - (0,835)→(60,850) 的穿越边在 t=20 进入温区（strict 在 t=60 前无点）；
    - 其后恒温 850°C，带内采样点止于 t=1832，strict 段跨度 1772 秒 < 1800；
    - linear 从 t=20 起累计，等效秒超过 1800。
    """
    records = [Record(0, 835.0), Record(60, 850.0)]
    t = 90
    while t <= 1830:
        records.append(Record(t, 850.0))
        t += 30
    records.append(Record(1832, 850.0))
    return records


def test_low_temp_penetration_linear_qualified_but_strict_not():
    records = low_temp_penetration_records()
    strict = analyze(records, MODE_STRICT)
    linear = analyze(records, MODE_LINEAR)

    assert strict["qualified"] is False
    assert strict["longestSegment"]["duration"] == 1832 - 60
    assert linear["qualified"] is True

    seg = linear["earliestQualifyingSegment"]
    # 连续段从穿越边上的带内交点开始：整数锚点 0 + 20.000 秒偏移
    assert seg["startAnchorT"] == 0
    assert abs(seg["startOffset"] - 20.0) < TOL
    assert abs(seg["equivalentSeconds"] - MIN_SOAK_SECONDS) < 1e-9

    # 首次达标时刻：t=1830 后还差约 1.146 等效秒 → 锚点 1830 + 十进制偏移
    assert seg["endAnchorT"] == 1830
    crossing_piece = 20.0 / LN2  # 穿越边带内片的等效秒
    expected_offset = MIN_SOAK_SECONDS - crossing_piece - 1770.0
    assert abs(seg["endOffset"] - expected_offset) < TOL
    assert 0.0 < seg["endOffset"] < 2.0

    # 用独立数值积分核验：从报告起点到报告终点，带内累计恰为 1800
    start = seg["startAnchorT"] + seg["startOffset"]
    end = seg["endAnchorT"] + seg["endOffset"]
    reached = numeric_integral_range(records, start, end)
    assert abs(reached - MIN_SOAK_SECONDS) < TOL


def test_constant_860_qualifies_in_900_physical_seconds():
    # 860°C 权重为 2：物理 900 秒即等效 1800 秒；strict 同炉次物理时长不足
    records = const_series(0, 16, 60, 860.0)  # 0..900
    linear = analyze(records, MODE_LINEAR)
    assert linear["qualified"] is True
    seg = linear["earliestQualifyingSegment"]
    assert seg["endAnchorT"] == 900 and seg["endOffset"] == 0.0
    assert abs(seg["equivalentSeconds"] - 1800.0) < 1e-9
    assert analyze(records, MODE_STRICT)["qualified"] is False


def test_first_hit_uses_earliest_segment():
    # 第一段不合格（短），带外间隔后第二段达标 → 取第二段
    first = const_series(0, 3, 30, 850.0)          # 0..60，仅 60 等效秒
    gap = [Record(200, 700.0)]                     # 带外切段
    second = const_series(300, 61, 30, 850.0)      # 300..2100 达标
    result = analyze(first + gap + second, MODE_LINEAR)
    assert result["qualified"] is True
    seg = result["earliestQualifyingSegment"]
    assert seg["startAnchorT"] == 300
    assert seg["endAnchorT"] == 2100 and seg["endOffset"] == 0.0


def test_linear_unqualified_reports_longest_equivalent():
    # 单线段穿越双边界但远不足 1800：43.28 等效秒
    records = [Record(0, 835.0), Record(60, 865.0)]
    result = analyze(records, MODE_LINEAR)
    assert result["qualified"] is False
    assert result["earliestQualifyingSegment"] is None
    longest = result["longestSegment"]
    assert abs(longest["equivalentSeconds"] - 30.0 / LN2) < TOL
    assert longest["startAnchorT"] == 0
    assert abs(longest["startOffset"] - 10.0) < 1e-9
    assert longest["endAnchorT"] == 0  # 终点在同一锚点 + 50 秒偏移
    assert abs(longest["endOffset"] - 50.0) < 1e-9


def test_no_in_band_piece_at_all():
    result = analyze(
        [Record(0, 700.0), Record(30, 720.0), Record(60, 750.0)], MODE_LINEAR
    )
    assert result["qualified"] is False
    assert result["segmentCount"] == 0
    assert result["longestSegment"] is None


def test_huge_anchors_keep_decimal_offset_precision():
    # 10^20 锚点：整数锚点精确保留，偏移仍是局部 10/50，绝不被浮点舍没
    base = 10**20
    result = analyze(
        [Record(base, 835.0), Record(base + 60, 865.0)], MODE_LINEAR
    )
    seg = result["longestSegment"]
    assert seg["startAnchorT"] == base
    assert abs(seg["startOffset"] - 10.0) < 1e-9
    assert seg["endAnchorT"] == base
    assert abs(seg["endOffset"] - 50.0) < 1e-9


# ---------------------------------------------------------------------------
# 模式快照与默认值
# ---------------------------------------------------------------------------


def test_snapshot_records_mode_and_equivalent_seconds():
    linear = analyze(const_series(0, 61, 30, 850.0), MODE_LINEAR)
    assert linear["analysisMode"] == MODE_LINEAR
    assert (
        linear["earliestQualifyingSegment"]["equivalentSeconds"] == 1800.0
    )
    strict = analyze(const_series(0, 61, 30, 850.0), MODE_STRICT)
    assert strict["analysisMode"] == MODE_STRICT
    assert strict["earliestQualifyingSegment"]["duration"] == 1800


def test_analyze_defaults_to_strict():
    records = const_series(0, 61, 30, 850.0)
    assert analyze(records)["analysisMode"] == MODE_STRICT


def test_normalize_analysis_mode():
    assert normalize_analysis_mode(None) == MODE_STRICT
    assert normalize_analysis_mode("") == MODE_STRICT
    assert normalize_analysis_mode("strict") == MODE_STRICT
    assert normalize_analysis_mode("linear_equivalent") == MODE_LINEAR
    for bad in ("LINEAR", "linear", "equivalent", "strict "):
        try:
            normalize_analysis_mode(bad)
        except Exception as exc:
            assert exc.code == "unknown_analysis_mode"
        else:
            raise AssertionError(f"{bad} 应被拒绝")
