"""Offline unit tests for the extraction/flatten/discover script helpers.

The scripts' live paths hit the BroodMinder API (and are covered by the
`live`-marked contract tests). These tests instead pin the *pure* helper
functions the scripts are built from — the ones extracted while reducing
cognitive complexity (issue #22) — so the refactor is provably
behavior-preserving and the scripts gain offline coverage.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discover  # noqa: E402
import extract_all  # noqa: E402
import flatten  # noqa: E402


# --------------------------------------------------------------------------
# discover.py
# --------------------------------------------------------------------------
def test_find_sample_ids_from_list():
    apiaries = [
        {"hives": [
            {"hiveId": "H1", "devices": [{"deviceId": "D1"}]},
            {"hiveId": "H2", "devices": [{"deviceId": "D2"}]},
        ]},
    ]
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_from_dict_wrapper_and_alt_keys():
    # dict wrapper + alternate id key spellings + "positions" instead of "devices"
    apiaries = {"apiaries": [
        {"hives": [{"id": "H9", "positions": [{"deviceID": "DV9"}]}]},
    ]}
    assert discover.find_sample_ids(apiaries) == ("H9", "DV9")


def test_find_sample_ids_empty():
    assert discover.find_sample_ids([]) == (None, None)


# --------------------------------------------------------------------------
# extract_all.py
# --------------------------------------------------------------------------
def test_filter_apiaries_no_filter_returns_all():
    apiaries = [{"name": "A", "apiaryId": "1"}, {"name": "B", "apiaryId": "2"}]
    assert extract_all.filter_apiaries(apiaries, []) == apiaries


def test_filter_apiaries_by_name_case_insensitive():
    apiaries = [{"name": "Backyard", "apiaryId": "1"}, {"name": "Roof", "apiaryId": "2"}]
    got = extract_all.filter_apiaries(apiaries, ["backyard"])
    assert [a["apiaryId"] for a in got] == ["1"]


def test_filter_apiaries_by_id():
    apiaries = [{"name": "Backyard", "apiaryId": "1"}, {"name": "Roof", "apiaryId": "2"}]
    got = extract_all.filter_apiaries(apiaries, ["2"])
    assert [a["apiaryId"] for a in got] == ["2"]


def test_update_empty_counter():
    # limit off -> counter left untouched (matches original guarded behavior)
    assert extract_all._update_empty(3, 0, 0) == 3
    # empty window increments
    assert extract_all._update_empty(2, 0, 3) == 3
    # non-empty window resets
    assert extract_all._update_empty(2, 5, 3) == 0


def test_hit_empty_limit():
    assert extract_all._hit_empty_limit(0, 99) is False   # feature off
    assert extract_all._hit_empty_limit(3, 2) is False
    assert extract_all._hit_empty_limit(3, 3) is True


def test_count_reading_rows_and_notes_unchanged():
    payload = [{"readings": [1, 2]}, {"readings": [3]}]
    assert extract_all.count_reading_rows(payload) == 3
    assert extract_all.count_notes([{"a": 1}]) == 1
    assert extract_all.count_notes({"notes": [1, 2]}) == 2
    assert extract_all.count_notes("nope") == 0


# --------------------------------------------------------------------------
# flatten.py
# --------------------------------------------------------------------------
def test_build_row_shape_and_metrics():
    meta = {"apiaryId": "A1", "apiaryName": "Yard", "hiveName": "Hive1"}
    reading = {
        "deviceId": "D1", "timestamp": 1_700_000_000,
        "batteryLevel": 90, "chargeRemaining": 88,
        "readings": {"t": 21.5, "h": 55},
    }
    row = flatten.build_row("H1", meta, "P1", reading)
    assert row["apiaryId"] == "A1"
    assert row["hiveId"] == "H1"
    assert row["positionID"] == "P1"
    assert row["deviceId"] == "D1"
    assert row["timestamp"] == 1_700_000_000
    assert row["datetime"].startswith("2023-11-14T")
    assert row["m_t"] == 21.5 and row["m_h"] == 55


def test_build_row_null_timestamp():
    row = flatten.build_row("H1", {}, "P1", {"timestamp": None, "readings": {}})
    assert row["datetime"] is None


def test_update_coverage_accumulates():
    c = {"rows": 0, "min_ts": None, "max_ts": None, "devices": set(), "positions": set()}
    flatten.update_coverage(c, {"deviceId": "D1"}, "P1", 200)
    flatten.update_coverage(c, {"deviceId": "D2"}, "P2", 100)
    assert c["rows"] == 2
    assert c["devices"] == {"D1", "D2"}
    assert c["positions"] == {"P1", "P2"}
    assert c["min_ts"] == 100 and c["max_ts"] == 200


def test_build_coverage_output():
    coverage = {"H1": {"rows": 2, "min_ts": 1_700_000_000, "max_ts": 1_700_000_100,
                       "devices": {"D1", None}, "positions": {"P1"}}}
    meta = {"H1": {"apiaryName": "Yard", "hiveName": "Hive1"}}
    out = flatten.build_coverage(coverage, meta)
    assert out["H1"]["rows"] == 2
    assert out["H1"]["devices"] == ["D1"]  # None filtered out, sorted
    assert out["H1"]["positions"] == ["P1"]
    assert out["H1"]["earliest"].startswith("2023-11-14T")
    assert out["H1"]["latest"] is not None


def test_discover_metric_keys_scans_gz_and_plain(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    plain = [{"readings": [{"readings": {"weight": 1, "temp": 2}}]}]
    (hdir / "0-1.readings.json").write_text(json.dumps(plain))
    gzpayload = [{"readings": [{"readings": {"humidity": 3}}]}]
    with gzip.open(hdir / "1-2.readings.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(gzpayload, fh)
    keys = flatten.discover_metric_keys(tmp_path)
    assert keys == ["humidity", "temp", "weight"]  # sorted, deduped
