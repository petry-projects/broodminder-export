"""Unit tests for the pure helpers extracted from the script `main()` functions.

These lock the behavior of the logic pulled out of `discover.main`,
`extract_all.main`, and `flatten.main` when their cognitive complexity was
reduced (SonarCloud python:S3776). They exercise the extracted helpers directly
— no API calls — so the refactor is provably behavior-preserving.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discover  # noqa: E402
import extract_all  # noqa: E402
import flatten  # noqa: E402


# ---------------------------------------------------------------------------
# discover.find_sample_ids
# ---------------------------------------------------------------------------
def test_find_sample_ids_from_list():
    apiaries = [
        {"hives": [
            {"hiveId": "H1", "devices": [{"deviceId": "D1"}, {"deviceId": "D2"}]},
            {"hiveId": "H2", "devices": [{"deviceId": "D3"}]},
        ]},
    ]
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_dict_wrapper():
    apiaries = {"apiaries": [{"hives": [{"hiveId": "H1", "devices": [{"deviceId": "D1"}]}]}]}
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_alternate_keys():
    # schemas vary: id / deviceID / positions instead of hiveId / deviceId / devices
    apiaries = [{"hives": [{"id": "H9", "positions": [{"deviceID": "D9"}]}]}]
    assert discover.find_sample_ids(apiaries) == ("H9", "D9")


def test_find_sample_ids_hive_without_devices():
    apiaries = [{"hives": [{"hiveId": "H1"}]}]
    assert discover.find_sample_ids(apiaries) == ("H1", None)


def test_find_sample_ids_empty():
    assert discover.find_sample_ids([]) == (None, None)
    assert discover.find_sample_ids({"apiaries": []}) == (None, None)
    assert discover.find_sample_ids(None) == (None, None)


# ---------------------------------------------------------------------------
# extract_all.select_apiaries
# ---------------------------------------------------------------------------
_APIARIES = [
    {"apiaryId": "A1", "name": "North Yard", "hives": []},
    {"apiaryId": "A2", "name": "South Yard", "hives": []},
]


def test_select_apiaries_no_filter_returns_all():
    assert extract_all.select_apiaries(_APIARIES, []) == _APIARIES


def test_select_apiaries_by_name_case_insensitive():
    got = extract_all.select_apiaries(_APIARIES, ["north yard"])
    assert [a["apiaryId"] for a in got] == ["A1"]


def test_select_apiaries_by_id_exact():
    got = extract_all.select_apiaries(_APIARIES, ["A2"])
    assert [a["apiaryId"] for a in got] == ["A2"]


def test_select_apiaries_no_match_is_empty():
    assert extract_all.select_apiaries(_APIARIES, ["nope"]) == []


# ---------------------------------------------------------------------------
# flatten.build_row
# ---------------------------------------------------------------------------
def test_build_row_full():
    meta = {"apiaryId": "A1", "apiaryName": "North", "hiveName": "Hive-1"}
    reading = {
        "deviceId": "D1",
        "timestamp": 1_700_000_000,
        "batteryLevel": 90,
        "chargeRemaining": 12,
        "readings": {"temperature": 34.5, "humidity": 55},
    }
    row = flatten.build_row("H1", meta, "P1", reading)
    assert row["apiaryId"] == "A1"
    assert row["apiaryName"] == "North"
    assert row["hiveId"] == "H1"
    assert row["hiveName"] == "Hive-1"
    assert row["positionID"] == "P1"
    assert row["deviceId"] == "D1"
    assert row["timestamp"] == 1_700_000_000
    assert row["datetime"] == "2023-11-14T22:13:20+00:00"
    assert row["batteryLevel"] == 90
    assert row["chargeRemaining"] == 12
    assert row["m_temperature"] == 34.5
    assert row["m_humidity"] == 55


def test_build_row_null_timestamp_gives_null_datetime():
    row = flatten.build_row("H1", {}, "P1", {"deviceId": "D1", "timestamp": None})
    assert row["timestamp"] is None
    assert row["datetime"] is None


def test_build_row_zero_timestamp_gives_epoch_datetime():
    row = flatten.build_row("H1", {}, "P1", {"deviceId": "D1", "timestamp": 0})
    assert row["timestamp"] == 0
    assert row["datetime"] == "1970-01-01T00:00:00+00:00"


def test_build_row_missing_metrics_has_no_metric_columns():
    row = flatten.build_row("H1", {}, "P1", {"deviceId": "D1", "timestamp": 1})
    assert not any(k.startswith("m_") for k in row)
