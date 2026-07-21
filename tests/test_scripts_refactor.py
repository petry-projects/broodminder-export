"""Unit tests for the pure helpers extracted from the extract scripts.

These pin the behavior of the small, side-effect-free functions carved out of
`main()` while reducing SonarCloud Cognitive Complexity (S3776). They run
offline (no API key, no network) and guard against regressions in the refactor.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import discover  # noqa: E402
import extract_all  # noqa: E402
import flatten  # noqa: E402


# ── discover.py ──────────────────────────────────────────────────────────────

def test_first_returns_first_present_key():
    assert discover.first({"b": 2, "a": 1}, "a", "b") == 1
    assert discover.first({"b": 2}, "a", "b") == 2
    assert discover.first({}, "a") is None
    assert discover.first(["not", "a", "dict"], "a") is None


def test_find_sample_ids_walks_tree():
    containers = [
        {"hives": [
            {"hiveId": "H1", "devices": [{"deviceId": "D1"}, {"deviceId": "D2"}]},
            {"hiveId": "H2", "devices": [{"deviceId": "D3"}]},
        ]},
    ]
    assert discover.find_sample_ids(containers) == ("H1", "D1")


def test_find_sample_ids_alternate_key_names():
    # Schemas vary: id/hiveID and positions/deviceID must still resolve.
    containers = [{"hives": [{"id": "HX", "positions": [{"id": "DX"}]}]}]
    assert discover.find_sample_ids(containers) == ("HX", "DX")


def test_find_sample_ids_empty_and_missing():
    assert discover.find_sample_ids([]) == (None, None)
    assert discover.find_sample_ids(None) == (None, None)
    assert discover.find_sample_ids([{"hives": [{"hiveId": "H1"}]}]) == ("H1", None)


# ── extract_all.py ───────────────────────────────────────────────────────────

def test_filter_apiaries_empty_filter_passthrough():
    apiaries = [{"name": "A", "apiaryId": "1"}, {"name": "B", "apiaryId": "2"}]
    assert extract_all.filter_apiaries(apiaries, []) is apiaries


def test_filter_apiaries_by_name_case_insensitive():
    apiaries = [{"name": "My Apiary", "apiaryId": "1"}, {"name": "Other", "apiaryId": "2"}]
    got = extract_all.filter_apiaries(apiaries, ["my apiary"])
    assert [a["apiaryId"] for a in got] == ["1"]


def test_filter_apiaries_by_id():
    apiaries = [{"name": "A", "apiaryId": "1"}, {"name": "B", "apiaryId": "2"}]
    got = extract_all.filter_apiaries(apiaries, ["2"])
    assert [a["apiaryId"] for a in got] == ["2"]


def test_count_reading_rows():
    payload = [{"readings": [1, 2, 3]}, {"readings": []}, {"readings": None}, {}]
    assert extract_all.count_reading_rows(payload) == 3
    assert extract_all.count_reading_rows({"not": "a list"}) == 0


def test_count_notes():
    assert extract_all.count_notes([1, 2, 3]) == 3
    assert extract_all.count_notes({"notes": [1, 2]}) == 2
    assert extract_all.count_notes({"notes": None}) == 0
    assert extract_all.count_notes("nope") == 0


def test_parse_date():
    assert extract_all.parse_date("2021-01-01") == 1609459200


# ── flatten.py ───────────────────────────────────────────────────────────────

def test_build_row_shapes_metrics():
    m = {"apiaryId": "AP", "apiaryName": "Home", "hiveName": "Alpha"}
    r = {
        "deviceId": "D1",
        "timestamp": 1609459200,
        "batteryLevel": 90,
        "chargeRemaining": 88,
        "readings": {"t": 20.5, "h": 55},
    }
    row = flatten.build_row(m, "H1", "P1", r)
    assert row["apiaryId"] == "AP"
    assert row["hiveId"] == "H1"
    assert row["positionID"] == "P1"
    assert row["deviceId"] == "D1"
    assert row["timestamp"] == 1609459200
    assert row["datetime"] == "2021-01-01T00:00:00+00:00"
    assert row["batteryLevel"] == 90
    assert row["m_t"] == 20.5
    assert row["m_h"] == 55


def test_build_row_null_timestamp():
    row = flatten.build_row({}, "H1", "P1", {"readings": {}})
    assert row["datetime"] is None
    assert row["timestamp"] is None


def _write_readings(hdir: Path, name: str, payload) -> None:
    hdir.mkdir(parents=True, exist_ok=True)
    with gzip.open(hdir / name, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)


def test_discover_metric_keys_and_iter_rows(tmp_path):
    raw = tmp_path / "raw"
    hdir = raw / "H1"
    payload = [
        {"positionID": "P1", "readings": [
            {"deviceId": "D1", "timestamp": 1, "readings": {"t": 1, "h": 2}},
            {"deviceId": "D1", "timestamp": 2, "readings": {"w": 3}},
        ]},
    ]
    _write_readings(hdir, "1-2.readings.json.gz", payload)

    assert flatten.discover_metric_keys(raw) == {"t", "h", "w"}

    rows = list(flatten.iter_rows(hdir))
    assert [pid for pid, _ in rows] == ["P1", "P1"]
    assert [r["timestamp"] for _, r in rows] == [1, 2]
