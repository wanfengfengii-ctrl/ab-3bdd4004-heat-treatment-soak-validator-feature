"""线性曲线等效保温判据。

- 相邻点（间隔 ≤ 60 秒）连成线段，裁剪 840–860 °C 的时间片；
- 片内对 2**((温度-850)/10) 按时间积分，连续段累计 1800 等效秒合格；
- 首次达标时刻由指数积分解析反函数求出；
- 带外区间或超 60 秒间隔结束连续段，边界相邻片在同一时刻拼接不重复计时。
积分值与首次达标偏移误差不超过 0.001 秒。
"""

import math

import pytest

from app.soak import (
    MODE_LINEAR_EQUIVALENT,
    MODE_STRICT,
    Record,
    analyze,
)


def le(records):
    return analyze(records, MODE_LINEAR_EQUIVALENT)


def ramp(t0, y0, t1, y1):
    return [Record(t=t0, temp=y0), Record(t=t1, temp=y1)]


def hold(start_t, end_t, step, temp):
    return [Record(t=t, temp=temp) for t in range(start_t, end_t + 1, step)]


# 权重 2**((T-850)/10) 在边界与参考点的取值
W840 = 0.5
W850 = 1.0
W860 = 2.0


def integral_linear(w_lo, w_hi, duration):
    """权重从 w_lo 指数变化到 w_hi、持续 duration 秒的解析积分。"""
    return duration * (w_hi - w_lo) / math.log(w_hi / w_lo)


def test_constant_hold_exactly_1800_qualified():
    # 850 °C 恒温：权重恒为 1，1800 秒恰好 1800 等效秒
    result = le(hold(0, 1800, 30, 850.0))
    assert result["qualified"] is True
    seg = result["earliestQualifyingSegment"]
    assert seg["equivalentSeconds"] == pytest.approx(1800.0, abs=1e-9)
    # 首次达标恰在段末记录点 t=1800：锚点为该点、偏移 0
    assert seg["reachT"] == 1800
    assert seg["reachOffset"] == pytest.approx(0.0, abs=1e-9)
    assert (seg["startT"], seg["startOffset"]) == (0, 0.0)
    assert (seg["endT"], seg["endOffset"]) == (1800, 0.0)


def test_constant_hold_just_short_unqualified():
    result = le(hold(0, 1770, 30, 850.0))
    assert result["qualified"] is False
    assert result["earliestQualifyingSegment"] is None
    assert result["longestSegment"]["equivalentSeconds"] == pytest.approx(1770.0)


def test_low_temperature_penetration_then_qualify():
    """低温穿入：第一条线段从带外穿入 840 °C 边界，其后恒温达标。

    0s@830 → 60s@850：840 出现在 30s，片 [30,60] 权重由 0.5 指数升到 1；
    随后 850 °C 恒温到 t=1860。累计等效秒 = 穿入片积分 + 1800 秒恒温。
    """
    records = ramp(0, 830.0, 60, 850.0) + hold(90, 1860, 30, 850.0)
    result = le(records)
    assert result["qualified"] is True
    seg = result["earliestQualifyingSegment"]
    entry_piece = integral_linear(W840, W850, 30.0)
    assert seg["equivalentSeconds"] == pytest.approx(entry_piece + 1800.0, abs=1e-9)
    # 连续段从穿入边界时刻 t=30 开始（非整条线段起点）
    assert (seg["startT"], seg["startOffset"]) == (0, 30.0)
    # 首次达标：先耗尽穿入片，再在恒温段补 (1800-entry_piece) 秒
    reach = 60 + (1800 - entry_piece)
    assert seg["reachT"] + seg["reachOffset"] == pytest.approx(reach, abs=0.001)
    # 首次达标早于段末
    assert seg["reachT"] + seg["reachOffset"] < seg["endT"]


def test_single_segment_crosses_both_boundaries():
    """单条线段自下而上穿越双边界：830@0 → 870@40（间隔 ≤ 60 秒）。

    840 在 10s、860 在 30s，片 [10,30] 权重 0.5→2，一次穿越不切段。
    """
    result = le(ramp(0, 830.0, 40, 870.0))
    assert result["segmentCount"] == 1
    seg = result["longestSegment"]
    assert (seg["startT"], seg["startOffset"]) == (0, 10.0)
    assert (seg["endT"], seg["endOffset"]) == (0, 30.0)
    assert seg["slices"] == 1
    expected = integral_linear(W840, W860, 20.0)
    assert seg["equivalentSeconds"] == pytest.approx(expected, abs=1e-9)
    assert result["qualified"] is False  # 仅 20 秒，远不足 1800


def test_crossing_down_through_both_boundaries():
    """自上而下穿越双边界同样裁剪为单片，积分对称一致。"""
    up = le(ramp(0, 830.0, 40, 870.0))["longestSegment"]["equivalentSeconds"]
    down = le(ramp(0, 870.0, 40, 830.0))["longestSegment"]["equivalentSeconds"]
    assert down == pytest.approx(up, abs=1e-9)


def test_pieces_join_at_boundary_without_double_counting():
    """两条相邻线段在同一记录点（温度恰为 840 边界）相接：两个正长度片必须
    拼成同一连续段，且在该点不重复计时。

    845@0→840@30（整片带内，结束于边界点 t=30），再 840@30→845@60
    （从边界点起整片带内）。锚点表示不同时会被误切成两段。
    """
    records = [Record(0, 845.0), Record(30, 840.0), Record(60, 845.0)]
    result = le(records)
    assert result["segmentCount"] == 1
    seg = result["longestSegment"]
    assert seg["slices"] == 2
    assert (seg["startT"], seg["startOffset"]) == (0, 0.0)
    assert (seg["endT"], seg["endOffset"]) == (60, 0.0)
    # 两个 [0,30] 的权重 2^(-0.5)→0.5 对称，合计时长恰 60 秒，边界点不重复
    assert seg["duration"] == pytest.approx(60.0, abs=1e-9)
    half = integral_linear(2.0 ** (-0.5), W840, 30.0)
    assert seg["equivalentSeconds"] == pytest.approx(2 * half, abs=1e-9)


def test_out_of_band_interval_cuts_segment():
    """带外下探：850@0→800@30→850@60，两侧带内片被切成两段。

    每边在降到 840（离开带）前为恒温权重 1，历时 6 秒（30 秒降 50 度，
    降 10 度到 840 需 6 秒），两段积分相等。
    """
    records = [
        Record(0, 850.0),
        Record(30, 800.0),
        Record(60, 850.0),
    ]
    result = le(records)
    assert result["segmentCount"] == 2
    longest = result["longestSegment"]
    # 每片 6 秒，权重由 1 指数降到 0.5（或反之）
    assert longest["duration"] == pytest.approx(6.0, abs=1e-9)
    assert longest["equivalentSeconds"] == pytest.approx(
        integral_linear(W850, W840, 6.0), abs=1e-9
    )


def test_gap_of_exactly_60_seconds_keeps_segment():
    result = le([Record(0, 850.0), Record(60, 850.0)])
    assert result["segmentCount"] == 1
    assert result["longestSegment"]["equivalentSeconds"] == pytest.approx(60.0)


def test_gap_over_60_seconds_cuts_segment():
    result = le([Record(0, 850.0), Record(61, 850.0)])
    assert result["segmentCount"] == 0
    assert result["qualified"] is False
    assert result["longestSegment"] is None


def test_long_gap_between_holds_cuts_into_two_segments():
    records = hold(0, 600, 30, 850.0) + hold(900, 1500, 30, 850.0)
    result = le(records)
    assert result["segmentCount"] == 2
    # 两段各 600 秒，均不足 1800
    assert result["qualified"] is False
    assert result["longestSegment"]["equivalentSeconds"] == pytest.approx(600.0)


def test_earliest_qualifying_segment_is_reported():
    # 第一段恒温达标，越界切段，第二段更长也必须报告最早的第一段
    first = hold(0, 1800, 30, 850.0)
    out = [Record(1900, 500.0)]
    second = hold(2000, 4000, 30, 850.0)
    result = le(first + out + second)
    seg = result["earliestQualifyingSegment"]
    assert (seg["startT"], seg["endT"]) == (0, 1800)
    assert seg["equivalentSeconds"] == pytest.approx(1800.0)


def test_first_reach_offset_via_analytic_inverse_within_ramp():
    """首次达标落在一条升温线段内：解析反函数求偏移，与数值积分一致。

    先恒温 1740 秒（1740 等效秒），再 850→860 用 60 秒升温（权重 1→2），
    所需 60 等效秒落在该线段内（整段仅约 86.6 等效秒）。
    """
    records = hold(0, 1740, 30, 850.0) + ramp(1740, 850.0, 1800, 860.0)
    result = le(records)
    seg = result["earliestQualifyingSegment"]
    assert result["qualified"] is True
    # 片内解析：W(s)=exp(k s), k=ln2/60；积分 (exp(k s)-1)/k = 60
    k = math.log(2.0) / 60.0
    s = math.log1p(60.0 * k) / k
    assert seg["reachT"] == 1740
    assert seg["reachOffset"] == pytest.approx(s, abs=0.001)
    # 与定义自洽：从段首到首次达标时刻积分恰为 1800
    assert 1740 + integral_linear(W850, 2.0 ** (s / 60.0), s) == pytest.approx(
        1800.0, abs=0.001
    )


def test_higher_temperature_counts_more_equivalent_seconds():
    # 860 °C 恒温权重 2：900 物理秒即 1800 等效秒
    result = le(hold(0, 900, 30, 860.0))
    assert result["qualified"] is True
    seg = result["earliestQualifyingSegment"]
    assert seg["equivalentSeconds"] == pytest.approx(1800.0, abs=1e-9)
    assert seg["reachT"] == 900


def test_lower_temperature_needs_longer_hold():
    # 840 °C 恒温权重 0.5：1800 物理秒只有 900 等效秒，不合格
    result = le(hold(0, 1800, 30, 840.0))
    assert result["qualified"] is False
    assert result["longestSegment"]["equivalentSeconds"] == pytest.approx(900.0)


def test_huge_anchor_keeps_offset_precision():
    # 超大整数锚点 + 小数偏移：锚点不与偏移混算，精度不丢失
    base = 10**20
    records = [Record(base, 830.0), Record(base + 60, 850.0)]
    seg = le(records)["longestSegment"]
    assert seg["startT"] == base
    assert seg["startOffset"] == pytest.approx(30.0)
    assert seg["endT"] == base + 60
    assert seg["endOffset"] == pytest.approx(0.0)


def test_strict_mode_shape_unchanged_semantics():
    # 默认严格判定不受等效模式影响：边界缓慢穿入在严格模式下整段无效
    records = ramp(0, 830.0, 60, 850.0) + hold(90, 1860, 30, 850.0)
    strict = analyze(records, MODE_STRICT)
    assert strict["analysisMode"] == MODE_STRICT
    # 首点 830 在带外，严格模式第一段从 t=60 开始
    assert strict["longestSegment"]["startT"] == 60
    linear = le(records)
    assert linear["analysisMode"] == MODE_LINEAR_EQUIVALENT
    # 同一份数据：线性等效把穿入段计入，连续段起点更早
    assert linear["longestSegment"]["startT"] == 0
