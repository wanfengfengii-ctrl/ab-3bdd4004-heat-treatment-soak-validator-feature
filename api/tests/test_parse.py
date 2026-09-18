"""解析与校验判据：格式错误、缺字段、乱序、非有限值、超限一律整份拒绝。"""

import json

import pytest

from app.soak import (
    MAX_FILE_BYTES,
    MAX_RECORDS,
    Rejection,
    Record,
    parse_payload,
)


def payload(records) -> bytes:
    return json.dumps(records).encode("utf-8")


def test_valid_minimal_payload():
    records = parse_payload(payload([{"t": 0, "temp": 850}, {"t": 30, "temp": 845.5}]))
    assert records == [Record(t=0, temp=850.0), Record(t=30, temp=845.5)]


def test_temp_accepts_int_and_float():
    records = parse_payload(payload([{"t": 0, "temp": 850}, {"t": 1, "temp": 850.25}]))
    assert records[0].temp == 850.0 and records[1].temp == 850.25


def test_empty_file_rejected():
    with pytest.raises(Rejection) as exc:
        parse_payload(b"")
    assert exc.value.code == "empty_file"


def test_file_over_2mib_rejected():
    oversized = b" " * (MAX_FILE_BYTES + 1)
    with pytest.raises(Rejection) as exc:
        parse_payload(oversized)
    assert exc.value.code == "file_too_large"


def test_file_at_exact_limit_accepted():
    body = payload([{"t": 0, "temp": 850}, {"t": 1, "temp": 851}])
    assert len(body) <= MAX_FILE_BYTES
    parse_payload(body)


def test_invalid_json_rejected():
    with pytest.raises(Rejection) as exc:
        parse_payload(b"[{not json]")
    assert exc.value.code == "invalid_json"


def test_non_utf8_rejected():
    with pytest.raises(Rejection) as exc:
        parse_payload(b"\xff\xfe[]")
    assert exc.value.code == "invalid_encoding"


@pytest.mark.parametrize("root", [{}, "text", 42, None, True])
def test_root_must_be_array(root):
    with pytest.raises(Rejection) as exc:
        parse_payload(payload(root))
    assert exc.value.code == "root_not_array"


@pytest.mark.parametrize("count", [0, 1, MAX_RECORDS + 1])
def test_record_count_bounds(count):
    data = [{"t": i, "temp": 850} for i in range(count)]
    with pytest.raises(Rejection) as exc:
        parse_payload(payload(data))
    assert exc.value.code == "record_count_out_of_range"


def test_max_record_count_accepted():
    data = [{"t": i, "temp": 850} for i in range(MAX_RECORDS)]
    assert len(parse_payload(payload(data))) == MAX_RECORDS


@pytest.mark.parametrize("item", [[1, 2], "x", 7, None])
def test_record_must_be_object(item):
    with pytest.raises(Rejection) as exc:
        parse_payload(payload([{"t": 0, "temp": 850}, item]))
    assert exc.value.code == "record_not_object"


@pytest.mark.parametrize("item", [{"temp": 850}, {"t": 1}, {}])
def test_missing_field_rejected(item):
    with pytest.raises(Rejection) as exc:
        parse_payload(payload([{"t": 0, "temp": 850}, item]))
    assert exc.value.code == "missing_field"


@pytest.mark.parametrize("bad_t", [1.5, "10", None, True, [1]])
def test_t_must_be_integer(bad_t):
    with pytest.raises(Rejection) as exc:
        parse_payload(payload([{"t": 0, "temp": 850}, {"t": bad_t, "temp": 850}]))
    assert exc.value.code == "invalid_t"


@pytest.mark.parametrize("bad_temp", ["850", None, True, [850]])
def test_temp_must_be_number(bad_temp):
    with pytest.raises(Rejection) as exc:
        parse_payload(payload([{"t": 0, "temp": 850}, {"t": 1, "temp": bad_temp}]))
    assert exc.value.code == "invalid_temp"


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_literal_rejected(token):
    raw = f'[{{"t": 0, "temp": 850}}, {{"t": 1, "temp": {token}}}]'.encode()
    with pytest.raises(Rejection) as exc:
        parse_payload(raw)
    assert exc.value.code == "non_finite_value"


def test_overflow_float_rejected():
    raw = b'[{"t": 0, "temp": 850}, {"t": 1, "temp": 1e999}]'
    with pytest.raises(Rejection) as exc:
        parse_payload(raw)
    assert exc.value.code == "non_finite_temp"


def test_timestamps_must_strictly_increase():
    with pytest.raises(Rejection) as exc:
        parse_payload(payload([{"t": 100, "temp": 850}, {"t": 100, "temp": 851}]))
    assert exc.value.code == "timestamps_not_strictly_increasing"
    with pytest.raises(Rejection) as exc:
        parse_payload(payload([{"t": 100, "temp": 850}, {"t": 99, "temp": 851}]))
    assert exc.value.code == "timestamps_not_strictly_increasing"


def test_extra_fields_ignored():
    records = parse_payload(
        payload([
            {"t": 0, "temp": 850, "sensor": "A"},
            {"t": 1, "temp": 851, "note": "ok"},
        ])
    )
    assert len(records) == 2
