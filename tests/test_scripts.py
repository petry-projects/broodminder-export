"""Unit tests for the pure helpers extracted from the CLI scripts.

These lock in the behavior of helpers pulled out of the flagged ``main()``
functions during the S3776 cognitive-complexity refactor. Unlike the live
contract tests, these run everywhere (no API key, no network).
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import discover, flatten  # noqa: E402


# --------------------------------------------------------------------------
# discover.find_sample_ids
# --------------------------------------------------------------------------
def test_find_sample_ids_bare_list():
    apiaries = [
        {"hives": [{"hiveId": "H1", "devices": [{"deviceId": "D1"}]}]},
    ]
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_alternate_keys():
    """Schemas vary: id/hiveID and positions/deviceID must also resolve."""
    apiaries = [
        {"hives": [{"id": "H2", "positions": [{"deviceID": "D2"}]}]},
    ]
    assert discover.find_sample_ids(apiaries) == ("H2", "D2")


def test_find_sample_ids_wrapped_dict():
    apiaries = {"apiaries": [{"hives": [{"hiveId": "H3", "devices": [{"id": "D3"}]}]}]}
    assert discover.find_sample_ids(apiaries) == ("H3", "D3")


def test_find_sample_ids_first_wins():
    apiaries = [
        {"hives": [
            {"hiveId": "H1", "devices": [{"deviceId": "D1"}]},
            {"hiveId": "H2", "devices": [{"deviceId": "D2"}]},
        ]},
    ]
    assert discover.find_sample_ids(apiaries) == ("H1", "D1")


def test_find_sample_ids_empty_and_missing():
    assert discover.find_sample_ids([]) == (None, None)
    assert discover.find_sample_ids([{"hives": []}]) == (None, None)
    # hive present, no devices -> device_id stays None
    assert discover.find_sample_ids([{"hives": [{"hiveId": "H1"}]}]) == ("H1", None)


# --------------------------------------------------------------------------
# flatten.build_row
# --------------------------------------------------------------------------
def test_build_row_flattens_metrics_and_datetime():
    meta = {"apiaryId": "A1", "apiaryName": "Yard", "hiveName": "Hive1"}
    r = {
        "deviceId": "D1",
        "timestamp": 1_700_000_000,  # 2023-11-14T22:13:20+00:00
        "batteryLevel": 90,
        "chargeRemaining": 4.1,
        "readings": {"t_C": 21.5, "rh": 55},
    }
    row = flatten.build_row("H1", meta, "P1", r)
    assert row["apiaryId"] == "A1"
    assert row["apiaryName"] == "Yard"
    assert row["hiveId"] == "H1"
    assert row["hiveName"] == "Hive1"
    assert row["positionID"] == "P1"
    assert row["deviceId"] == "D1"
    assert row["timestamp"] == 1_700_000_000
    assert row["datetime"] == "2023-11-14T22:13:20+00:00"
    assert row["batteryLevel"] == 90
    assert row["chargeRemaining"] == 4.1
    assert row["m_t_C"] == 21.5
    assert row["m_rh"] == 55


def test_build_row_null_timestamp_and_metrics():
    row = flatten.build_row("H1", {}, "P1", {"timestamp": None, "readings": None})
    assert row["datetime"] is None
    assert row["timestamp"] is None
    # no metric columns when readings is null
    assert not any(k.startswith("m_") for k in row)
    # missing meta -> None, not KeyError
    assert row["apiaryId"] is None
    assert row["hiveName"] is None


# --------------------------------------------------------------------------
# flatten.discover_metric_keys
# --------------------------------------------------------------------------
def _write_readings(path: Path, payload, gz: bool):
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh)
    else:
        path.write_text(json.dumps(payload))


def test_discover_metric_keys(tmp_path):
    raw = tmp_path / "raw"
    hdir = raw / "H1"
    hdir.mkdir(parents=True)
    _write_readings(
        hdir / "0-1.readings.json",
        [{"positionID": "P1", "readings": [{"readings": {"t_C": 1, "rh": 2}}]}],
        gz=False,
    )
    _write_readings(
        hdir / "1-2.readings.json.gz",
        [{"positionID": "P1", "readings": [{"readings": {"weight": 3}}]}],
        gz=True,
    )
    keys = flatten.discover_metric_keys(raw)
    assert keys == ["rh", "t_C", "weight"]  # sorted, deduped across files
