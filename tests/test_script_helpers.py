"""Offline unit tests for the extraction/flatten/discover script helpers.

The scripts' live paths hit the BroodMinder API (and are covered by the
`live`-marked contract tests). These tests instead pin the *pure* helper
functions the scripts are built from — the ones extracted while reducing
cognitive complexity (issue #22) — so the refactor is provably
behavior-preserving and the scripts gain offline coverage.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discover  # noqa: E402
import extract_all  # noqa: E402
import flatten  # noqa: E402

sys.path.insert(0, str(ROOT))
from bm.client import BroodMinderError  # noqa: E402


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


# --------------------------------------------------------------------------
# discover.py — probe()
# --------------------------------------------------------------------------
def test_probe_success(capsys):
    out = {}
    discover.probe(out, lambda: {"key": "val"}, "→ GET /example",
                   "sample_key", "err_key", "label", 100)
    cap = capsys.readouterr()
    assert "→ GET /example" in cap.out
    assert out["sample_key"] == {"key": "val"}
    assert "err_key" not in out


def test_probe_error(capsys):
    def bad_fetch():
        raise BroodMinderError(500, "GET", "http://x", "server error")

    out = {}
    discover.probe(out, bad_fetch, "→ GET /example",
                   "sample_key", "err_key", "readings", 100)
    cap = capsys.readouterr()
    assert "readings error" in cap.out
    assert "err_key" in out
    assert "sample_key" not in out


# --------------------------------------------------------------------------
# extract_all.py — report_window(), fetch_window(), process_hive()
# --------------------------------------------------------------------------
def test_report_window_silent_on_no_data(capsys):
    extract_all.report_window({"name": "Ap"}, {"name": "H"}, 0, 86400,
                              {"reading_rows": 0})
    assert capsys.readouterr().out == ""


def test_report_window_prints_rows_and_notes(capsys):
    extract_all.report_window({"name": "Ap"}, {"name": "H"},
                              1_700_000_000, 1_700_086_400,
                              {"reading_rows": 12, "notes": 3})
    out = capsys.readouterr().out
    assert "rows=12" in out
    assert "notes=3" in out


def test_report_window_prints_when_only_notes(capsys):
    extract_all.report_window({"name": "Ap"}, {"name": "H"},
                              1_700_000_000, 1_700_086_400,
                              {"reading_rows": 0, "notes": 5})
    out = capsys.readouterr().out
    assert "notes=5" in out


def test_fetch_window_records_readings(tmp_path):
    class FakeBM:
        call_count = 0

        def hive_readings(self, hid, s, e):
            return [{"readings": [{"readings": {}, "timestamp": 1}]}]

    args = argparse.Namespace(no_notes=True)
    completed = {}
    rec = extract_all.fetch_window(
        FakeBM(), {"apiaryId": "A1", "name": "Ap"}, {"name": "H"},
        "H1", tmp_path / "H1", "H1|0|100", 0, 100, completed, args)
    assert rec["reading_rows"] == 1
    assert completed["H1|0|100"] is rec


def test_fetch_window_includes_notes(tmp_path):
    class FakeBM:
        call_count = 0

        def hive_readings(self, hid, s, e):
            return []

        def hive_notes(self, hid, s, e):
            return [{"text": "n1"}, {"text": "n2"}]

    args = argparse.Namespace(no_notes=False)
    completed = {}
    rec = extract_all.fetch_window(
        FakeBM(), {"apiaryId": "A1", "name": "Ap"}, {"name": "H"},
        "H1", tmp_path / "H1", "H1|0|100", 0, 100, completed, args)
    assert rec["notes"] == 2


def test_process_hive_skips_completed(tmp_path):
    class FakeBM:
        call_count = 0

    args = argparse.Namespace(max_calls=900, no_notes=True, stop_after_empty=0)
    completed = {"H1|0|100": {"reading_rows": 5}}
    result = extract_all.process_hive(
        FakeBM(), {"name": "Ap", "apiaryId": "1"}, {"hiveId": "H1", "name": "H"},
        [(0, 100)], completed, args, tmp_path, lambda: None)
    assert result is False


def test_process_hive_budget_reached(tmp_path, capsys):
    class FakeBM:
        call_count = 900

    args = argparse.Namespace(max_calls=900, no_notes=True, stop_after_empty=0)
    completed = {}
    result = extract_all.process_hive(
        FakeBM(), {"name": "Ap", "apiaryId": "1"}, {"hiveId": "H1", "name": "H"},
        [(0, 100)], completed, args, tmp_path, lambda: None)
    assert result is True
    assert "budget reached" in capsys.readouterr().out


def test_process_hive_fetches_new_window(tmp_path):
    class FakeBM:
        call_count = 0

        def hive_readings(self, hid, s, e):
            return []

    args = argparse.Namespace(max_calls=900, no_notes=True, stop_after_empty=0)
    completed = {}
    result = extract_all.process_hive(
        FakeBM(), {"name": "Ap", "apiaryId": "1"}, {"hiveId": "H1", "name": "H"},
        [(0, 100)], completed, args, tmp_path, lambda: None)
    assert result is False
    assert len(completed) == 1


def test_process_hive_stop_after_empty(tmp_path):
    class FakeBM:
        call_count = 0

        def hive_readings(self, hid, s, e):
            return []

    args = argparse.Namespace(max_calls=900, no_notes=True, stop_after_empty=2)
    completed = {}
    result = extract_all.process_hive(
        FakeBM(), {"name": "Ap", "apiaryId": "1"}, {"hiveId": "H1", "name": "H"},
        [(0, 100), (100, 200), (200, 300)], completed, args, tmp_path, lambda: None)
    assert result is False
    assert len(completed) == 2  # stopped after 2 consecutive empty windows


def test_process_hive_breaks_on_cached_empty_limit(tmp_path):
    class FakeBM:
        call_count = 0

    args = argparse.Namespace(max_calls=900, no_notes=True, stop_after_empty=2)
    # All windows already completed but both empty → should break early
    completed = {
        "H1|0|100": {"reading_rows": 0},
        "H1|100|200": {"reading_rows": 0},
        "H1|200|300": {"reading_rows": 5},
    }
    result = extract_all.process_hive(
        FakeBM(), {"name": "Ap", "apiaryId": "1"}, {"hiveId": "H1", "name": "H"},
        [(0, 100), (100, 200), (200, 300)], completed, args, tmp_path, lambda: None)
    assert result is False
    assert len(completed) == 3  # no new fetches; stopped at cached limit


def test_process_hive_calls_save_manifest_at_25(tmp_path):
    save_calls = []

    class FakeBM:
        call_count = 0

        def hive_readings(self, hid, s, e):
            return []

    args = argparse.Namespace(max_calls=900, no_notes=True, stop_after_empty=0)
    completed = {}
    windows = [(i * 100, (i + 1) * 100) for i in range(25)]
    extract_all.process_hive(
        FakeBM(), {"name": "Ap", "apiaryId": "1"}, {"hiveId": "H1", "name": "H"},
        windows, completed, args, tmp_path, lambda: save_calls.append(1))
    assert len(save_calls) == 1  # triggered when len(completed) == 25


# --------------------------------------------------------------------------
# flatten.py — iter_positions(), iter_reading_rows(), write_readings(),
#              write_notes()
# --------------------------------------------------------------------------
def test_iter_positions_yields_pid_and_readings(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    payload = [
        {"positionID": "P1", "readings": [{"t": 1}]},
        {"positionID": "P2", "readings": None},
    ]
    (hdir / "0-1.readings.json").write_text(json.dumps(payload))
    results = list(flatten.iter_positions(hdir))
    assert ("P1", [{"t": 1}]) in results
    assert ("P2", []) in results


def test_iter_reading_rows_deduplicates(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    payload = [{"positionID": "P1", "readings": [
        {"deviceId": "D1", "timestamp": 100, "readings": {}},
        {"deviceId": "D1", "timestamp": 100, "readings": {}},  # duplicate
        {"deviceId": "D1", "timestamp": 200, "readings": {}},
    ]}]
    (hdir / "0-300.readings.json").write_text(json.dumps(payload))
    rows = list(flatten.iter_reading_rows(tmp_path, {}))
    assert len(rows) == 2


def test_iter_reading_rows_skips_non_dirs(tmp_path):
    (tmp_path / "loose_file.txt").write_text("not a dir")
    hdir = tmp_path / "H1"
    hdir.mkdir()
    payload = [{"positionID": "P1", "readings": [
        {"deviceId": "D1", "timestamp": 100, "readings": {}},
    ]}]
    (hdir / "0-1.readings.json").write_text(json.dumps(payload))
    rows = list(flatten.iter_reading_rows(tmp_path, {}))
    assert len(rows) == 1


def test_discover_metric_keys_skips_non_dirs(tmp_path):
    (tmp_path / "loose_file.txt").write_text("not a dir")
    hdir = tmp_path / "H1"
    hdir.mkdir()
    payload = [{"readings": [{"readings": {"weight": 1}}]}]
    (hdir / "0-1.readings.json").write_text(json.dumps(payload))
    keys = flatten.discover_metric_keys(tmp_path)
    assert keys == ["weight"]


def test_write_readings_streams_to_ndjson(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    payload = [{"positionID": "P1", "readings": [
        {"deviceId": "D1", "timestamp": 1_700_000_000,
         "batteryLevel": 90, "readings": {"t": 21}},
    ]}]
    (hdir / "0-1.readings.json").write_text(json.dumps(payload))
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    buf = io.StringIO()
    n = flatten.write_readings(tmp_path, {}, ["hiveId", "deviceId", "timestamp", "m_t"],
                               buf, None, coverage)
    assert n == 1
    row = json.loads(buf.getvalue().strip())
    assert row["hiveId"] == "H1"
    assert row["m_t"] == 21
    assert coverage["H1"]["rows"] == 1


def test_write_readings_with_csv_writer(tmp_path):
    import csv
    hdir = tmp_path / "H1"
    hdir.mkdir()
    payload = [{"positionID": "P1", "readings": [
        {"deviceId": "D1", "timestamp": 1_700_000_000, "batteryLevel": 90, "readings": {}},
    ]}]
    (hdir / "0-1.readings.json").write_text(json.dumps(payload))
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    cols = ["hiveId", "deviceId", "timestamp"]
    ndjson_buf = io.StringIO()
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=cols)
    writer.writeheader()
    n = flatten.write_readings(tmp_path, {}, cols, ndjson_buf, writer, coverage)
    assert n == 1
    csv_buf.seek(0)
    rows = list(csv.DictReader(csv_buf))
    assert rows[0]["hiveId"] == "H1"


def test_write_notes_from_list_payload(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    (hdir / "0-1.notes.json").write_text(json.dumps([{"text": "n1"}, {"text": "n2"}]))
    out_path = tmp_path / "notes.ndjson"
    n = flatten.write_notes(tmp_path, {"H1": {"hiveName": "Hive1"}}, out_path)
    assert n == 2
    lines = [json.loads(l) for l in out_path.read_text().splitlines()]
    assert lines[0]["hiveId"] == "H1" and lines[0]["hiveName"] == "Hive1"
    assert lines[0]["text"] == "n1"


def test_write_notes_from_dict_payload(tmp_path):
    hdir = tmp_path / "H1"
    hdir.mkdir()
    (hdir / "0-1.notes.json").write_text(json.dumps({"notes": [{"text": "dict-note"}]}))
    out_path = tmp_path / "notes.ndjson"
    n = flatten.write_notes(tmp_path, {}, out_path)
    assert n == 1
    assert json.loads(out_path.read_text().strip())["text"] == "dict-note"
