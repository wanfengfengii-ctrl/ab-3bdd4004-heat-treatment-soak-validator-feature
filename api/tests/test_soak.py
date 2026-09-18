"""区间判定判据：闭区间温度、60 秒间隔、1800 秒达标、最早达标段与最长段。"""

from app.soak import Record, analyze, find_segments


def series(start_t, count, step, temp):
    return [Record(t=start_t + i * step, temp=temp) for i in range(count)]


def test_single_continuous_segment_qualified():
    # 每 30 秒一点，61 点覆盖 0..1800，时长恰为 1800 秒 → 合格
    result = analyze(series(0, 61, 30, 850.0))
    assert result["qualified"] is True
    seg = result["earliestQualifyingSegment"]
    assert seg["startT"] == 0 and seg["endT"] == 1800
    assert seg["duration"] == 1800


def test_duration_just_below_threshold_unqualified():
    # 60 点覆盖 0..1770，时长 1770 < 1800 → 不合格
    result = analyze(series(0, 60, 30, 850.0))
    assert result["qualified"] is False
    assert result["earliestQualifyingSegment"] is None
    assert result["longestSegment"]["duration"] == 1770


def test_boundary_temperatures_inclusive():
    records = series(0, 10, 30, 840.0) + series(300, 10, 30, 860.0)
    segments = find_segments(records)
    assert len(segments) == 1
    assert segments[0][-1].t == 570


def test_out_of_range_temperature_cuts_segment():
    records = (
        series(0, 5, 30, 850.0)
        + [Record(t=150, temp=861.0)]
        + series(180, 5, 30, 850.0)
    )
    segments = find_segments(records)
    assert len(segments) == 2
    assert (segments[0][0].t, segments[0][-1].t) == (0, 120)
    assert (segments[1][0].t, segments[1][-1].t) == (180, 300)


def test_gap_over_60_seconds_cuts_segment():
    # 断档：两点间隔 300 秒，温度都在区间内也要切段
    records = series(0, 5, 30, 850.0) + series(420, 5, 30, 850.0)
    segments = find_segments(records)
    assert len(segments) == 2
    assert (segments[0][0].t, segments[0][-1].t) == (0, 120)
    assert (segments[1][0].t, segments[1][-1].t) == (420, 540)


def test_gap_of_exactly_60_seconds_keeps_segment():
    records = [Record(t=0, temp=850.0), Record(t=60, temp=850.0)]
    segments = find_segments(records)
    assert len(segments) == 1


def test_earliest_qualifying_segment_reported():
    # 两段都达标，必须给出最早的一段
    first = series(0, 61, 30, 850.0)          # 0..1800 达标
    gap = [Record(t=1900, temp=500.0)]        # 越界切段
    second = series(2000, 61, 30, 850.0)      # 2000..3800 达标
    result = analyze(first + gap + second)
    assert result["qualified"] is True
    seg = result["earliestQualifyingSegment"]
    assert (seg["startT"], seg["endT"]) == (0, 1800)


def test_longest_segment_reported_when_unqualified():
    short = series(0, 5, 30, 850.0)           # 时长 120
    out = [Record(t=200, temp=900.0)]
    longer = series(300, 10, 30, 850.0)       # 时长 270
    result = analyze(short + out + longer)
    assert result["qualified"] is False
    longest = result["longestSegment"]
    assert (longest["startT"], longest["endT"], longest["duration"]) == (300, 570, 270)


def test_no_valid_segment_at_all():
    result = analyze([Record(t=0, temp=700.0), Record(t=30, temp=950.0)])
    assert result["qualified"] is False
    assert result["segmentCount"] == 0
    assert result["longestSegment"] is None


def test_single_point_segment_has_zero_duration():
    records = [Record(t=0, temp=850.0), Record(t=30, temp=500.0)]
    result = analyze(records)
    assert result["qualified"] is False
    assert result["longestSegment"]["duration"] == 0
    assert result["longestSegment"]["points"] == 1
