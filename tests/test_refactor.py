"""Unit tests for the pure helpers used by the extraction scripts.

These cover the behavior-preserving refactor of the three `main()` functions
flagged by SonarCloud python:S3776 (cognitive complexity). Unlike the live
contract tests, these need no API key and run in CI, so they lock in the exact
behavior of the extracted helpers across the refactor.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discover  # noqa: E402
import extract_all  # noqa: E402
import flatten  # noqa: E402


# ── discover.py ──────────────────────────────────────────────────────────────
def test_find_sample_ids_from_list():
    apiaries = [
        {"hives": [
            {"hiveId": "H1", "devices": [{"deviceId": "D1"}, {"deviceId": "D2"}]},
            {"hiveId": "H2", "devices": [{"deviceId": "D3"}]},
        ]},
    ]
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_wrapped_dict_and_alt_keys():
    """Accepts {"apiaries": [...]} and the alternate id/position key names."""
    apiaries = {"apiaries": [
        {"hives": [{"id": "H9", "positions": [{"id": "P7"}]}]},
    ]}
    assert discover.find_sample_ids(apiaries) == ("H9", "P7")


def test_find_sample_ids_empty():
    assert discover.find_sample_ids([]) == (None, None)
    assert discover.find_sample_ids([{"hives": []}]) == (None, None)


# ── extract_all.py ───────────────────────────────────────────────────────────
def _apiaries():
    return [
        {"apiaryId": "A1", "name": "North Yard", "hives": [{"hiveId": "H1"}, {"hiveId": "H2"}]},
        {"apiaryId": "A2", "name": "South Yard", "hives": [{"hiveId": "H3"}]},
    ]


def test_select_hives_no_filter_flattens_all():
    hives = extract_all.select_hives(_apiaries(), [])
    assert [h["hiveId"] for _, h in hives] == ["H1", "H2", "H3"]


def test_select_hives_filter_by_name_case_insensitive():
    hives = extract_all.select_hives(_apiaries(), ["north yard"])
    assert [h["hiveId"] for _, h in hives] == ["H1", "H2"]


def test_select_hives_filter_by_apiary_id():
    hives = extract_all.select_hives(_apiaries(), ["A2"])
    assert [h["hiveId"] for _, h in hives] == ["H3"]


def test_count_reading_rows_and_notes():
    payload = [{"readings": [1, 2, 3]}, {"readings": []}, {"readings": [4]}]
    assert extract_all.count_reading_rows(payload) == 4
    assert extract_all.count_reading_rows({}) == 0
    assert extract_all.count_notes([{"a": 1}, {"b": 2}]) == 2
    assert extract_all.count_notes({"notes": [1, 2, 3]}) == 3
    assert extract_all.count_notes(None) == 0


def test_parse_date_utc_epoch():
    assert extract_all.parse_date("2021-01-01") == 1609459200


# ── flatten.py ───────────────────────────────────────────────────────────────
def test_hive_meta_keyed_by_hive_id():
    manifest = {"completed": {
        "H1|0|100": {"apiaryName": "N", "apiaryId": "A1", "hiveName": "Alpha"},
        "H1|100|200": {"apiaryName": "N", "apiaryId": "A1", "hiveName": "Alpha"},
    }}
    meta = flatten.hive_meta(manifest)
    assert meta == {"H1": {"apiaryName": "N", "apiaryId": "A1", "hiveName": "Alpha"}}


def test_build_row_prefixes_metrics_and_formats_datetime():
    m = {"apiaryId": "A1", "apiaryName": "North", "hiveName": "Alpha"}
    r = {"deviceId": "D1", "timestamp": 1609459200, "batteryLevel": 90,
         "chargeRemaining": 80, "readings": {"temp": 21.5, "hum": 55}}
    row = flatten.build_row("H1", m, "P1", r)
    assert row["hiveId"] == "H1"
    assert row["positionID"] == "P1"
    assert row["deviceId"] == "D1"
    assert row["m_temp"] == 21.5
    assert row["m_hum"] == 55
    assert row["datetime"] == "2021-01-01T00:00:00+00:00"


def test_build_row_null_timestamp_gives_null_datetime():
    row = flatten.build_row("H1", {}, "P1", {"timestamp": None, "readings": {}})
    assert row["datetime"] is None
    assert row["timestamp"] is None


def test_build_coverage_output():
    coverage = {"H1": {"rows": 2, "min_ts": 1609459200, "max_ts": 1609545600,
                       "devices": {"D1", None}, "positions": {"P1"}}}
    meta = {"H1": {"apiaryName": "North", "hiveName": "Alpha"}}
    out = flatten.build_coverage(coverage, meta)
    assert out["H1"]["rows"] == 2
    assert out["H1"]["devices"] == ["D1"]  # None filtered out
    assert out["H1"]["positions"] == ["P1"]
    assert out["H1"]["earliest"] == "2021-01-01T00:00:00+00:00"
    assert out["H1"]["latest"] == "2021-01-02T00:00:00+00:00"


def test_discover_metric_keys(tmp_path):
    import gzip
    import json
    hdir = tmp_path / "H1"
    hdir.mkdir()
    payload = [{"positionID": "P1", "readings": [
        {"readings": {"temp": 1, "hum": 2}},
        {"readings": {"weight": 3}},
    ]}]
    with gzip.open(hdir / "0-100.readings.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    keys = flatten.discover_metric_keys(tmp_path)
    assert keys == {"temp", "hum", "weight"}
