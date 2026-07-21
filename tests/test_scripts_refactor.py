"""Unit tests for the pure helpers extracted from the extract scripts.

These pin the behavior of the small, side-effect-free functions carved out of
`main()` while reducing SonarCloud Cognitive Complexity (S3776). They run
offline (no API key, no network) and guard against regressions in the refactor.
"""

from __future__ import annotations

import gzip
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

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


# ── discover.py: sample_endpoint ──────────────────────────────────────────────

def test_sample_endpoint_success():
    out = {}
    fetch_data = {"data": "sample"}
    with patch("builtins.print") as mock_print:
        discover.sample_endpoint(out, "test_key", "Test Header", lambda: fetch_data, 500)
    assert out["test_key_sample"] == fetch_data
    assert "test_key_error" not in out
    assert mock_print.call_args_list[0] == call("Test Header")


def test_sample_endpoint_handles_error():
    out = {}
    error = discover.BroodMinderError(500, "GET", "http://api.test/hive/123", "Internal Error")
    def fetch_with_error():
        raise error
    with patch("builtins.print") as mock_print:
        discover.sample_endpoint(out, "hive_readings", "Fetch Hives", fetch_with_error, 1000)
    assert out["hive_readings_error"] == str(error)
    assert "hive_readings_sample" not in out


def test_sample_endpoint_truncates_output():
    out = {}
    large_data = [{"id": i, "data": "x" * 100} for i in range(10)]
    with patch("builtins.print"):
        discover.sample_endpoint(out, "data", "Header", lambda: large_data, 50)
    assert out["data_sample"] == large_data


# ── extract_all.py: refactored helpers ───────────────────────────────────────

def test_empty_break_returns_count_and_flag():
    # With threshold, count increments on 0 rows
    consecutive, should_break = extract_all._empty_break(0, 3, 5)
    assert consecutive == 4 and should_break is False
    # Break when count reaches threshold
    consecutive, should_break = extract_all._empty_break(0, 4, 5)
    assert consecutive == 5 and should_break is True
    # Reset count on non-empty rows
    consecutive, should_break = extract_all._empty_break(10, 4, 5)
    assert consecutive == 0 and should_break is False


def test_empty_break_disabled_with_zero_threshold():
    consecutive, should_break = extract_all._empty_break(0, 10, 0)
    assert consecutive == 10 and should_break is False


def test_process_window_creates_directory_and_writes_files(tmp_path):
    bm = Mock()
    readings_data = [
        {"positionID": "P1", "readings": [1, 2, 3]},
        {"positionID": "P2", "readings": []},
    ]
    notes_data = [1, 2]
    bm.hive_readings = Mock(return_value=readings_data)
    bm.hive_notes = Mock(return_value=notes_data)
    apiary = {"apiaryId": "AP1", "name": "Home"}
    hive = {"name": "Hive A"}
    args = Mock(no_notes=False)
    hdir = tmp_path / "H1"

    rec = extract_all.process_window(bm, apiary, hive, "H1", hdir, 100, 200, args)

    assert hdir.exists()
    assert rec["apiaryId"] == "AP1"
    assert rec["apiaryName"] == "Home"
    assert rec["hiveName"] == "Hive A"
    assert rec["reading_rows"] == 3
    assert rec["notes"] == 2
    assert (hdir / "100-200.readings.json.gz").exists()
    assert (hdir / "100-200.notes.json.gz").exists()


def test_process_window_skips_notes_when_disabled(tmp_path):
    bm = Mock()
    bm.hive_readings = Mock(return_value=[{"positionID": "P1", "readings": []}])
    args = Mock(no_notes=True)
    hdir = tmp_path / "H1"

    rec = extract_all.process_window(bm, {}, {}, "H1", hdir, 100, 200, args)

    assert "notes" not in rec
    assert not (hdir / "100-200.notes.json.gz").exists()


def test_log_window_prints_for_non_empty():
    apiary = {"name": "Home"}
    hive = {"name": "Alpha"}
    rec = {"reading_rows": 5, "notes": 2}
    with patch("builtins.print") as mock_print:
        extract_all.log_window(apiary, hive, 1609459200, 1609545600, rec)
    # Verify print was called (output contains times and counts)
    mock_print.assert_called_once()
    output = mock_print.call_args[0][0]
    assert "Alpha" in output and "Home" in output


def test_log_window_skips_empty():
    apiary = {"name": "Home"}
    hive = {"name": "Alpha"}
    rec = {"reading_rows": 0, "notes": 0}
    with patch("builtins.print") as mock_print:
        extract_all.log_window(apiary, hive, 100, 200, rec)
    mock_print.assert_not_called()


def test_walk_hive_skips_completed_windows(tmp_path):
    bm = Mock()
    bm.call_count = 10
    bm.hive_readings = Mock(return_value=[{"positionID": "P1", "readings": [1, 2, 3]}])
    apiary = {"apiaryId": "AP1", "name": "Home"}
    hive = {"hiveId": "H1", "name": "Hive A"}
    completed = {"H1|100|200": {"reading_rows": 5}}
    args = Mock(max_calls=1000, stop_after_empty=0, no_notes=True)
    raw = tmp_path
    save_manifest = Mock()

    with patch("extract_all.log_window"):
        extract_all.walk_hive(bm, apiary, hive, [(100, 200), (300, 400)], completed, args, raw, save_manifest)

    # Should process uncompleted window but skip completed one
    assert bm.hive_readings.call_count == 1


def test_walk_hive_raises_budget_reached(tmp_path):
    bm = Mock()
    bm.call_count = 1000
    bm.hive_readings = Mock(return_value=[])
    apiary = {"apiaryId": "AP1", "name": "Home"}
    hive = {"hiveId": "H1", "name": "Hive A"}
    completed = {}
    args = Mock(max_calls=1000, stop_after_empty=0, no_notes=True)
    raw = tmp_path
    save_manifest = Mock()

    with patch("extract_all.log_window"):
        with patch("builtins.print"):
            try:
                extract_all.walk_hive(bm, apiary, hive, [(100, 200)], completed, args, raw, save_manifest)
                assert False, "Should have raised _BudgetReached"
            except extract_all._BudgetReached:
                pass


def test_walk_hive_early_exit_on_empty_windows(tmp_path):
    bm = Mock()
    bm.call_count = 10
    bm.hive_readings = Mock(return_value=[{"positionID": "P1", "readings": []}])
    apiary = {"apiaryId": "AP1", "name": "Home"}
    hive = {"hiveId": "H1", "name": "Hive A"}
    completed = {}
    args = Mock(max_calls=1000, stop_after_empty=2, no_notes=True)
    raw = tmp_path
    save_manifest = Mock()

    with patch("extract_all.log_window"):
        with patch("builtins.print"):
            extract_all.walk_hive(bm, apiary, hive, [(100, 200), (300, 400), (500, 600)], completed, args, raw, save_manifest)

    # Should stop after 2 empty windows, not process all 3
    assert bm.hive_readings.call_count < 3
