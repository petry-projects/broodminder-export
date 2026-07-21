"""Behavior-locking unit tests for the helpers extracted while reducing the
cognitive complexity of the three script `main()` functions (issue #56).

These are pure/offline tests (no live API, no `live` marker) so they run in CI
and guarantee the S3776 refactor preserved behavior exactly.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


discover = _load_script("discover")
extract_all = _load_script("extract_all")
flatten = _load_script("flatten")


# ==========================================================================
# discover.py
# ==========================================================================
def test_walk_sample_ids_list_form():
    apiaries = [
        {"hives": [
            {"hiveId": "H1", "devices": [{"deviceId": "D1"}]},
            {"hiveId": "H2", "devices": [{"deviceId": "D2"}]},
        ]},
    ]
    hive_id, device_id = discover.walk_sample_ids(apiaries)
    assert hive_id == "H1"
    assert device_id == "D1"


def test_walk_sample_ids_dict_form_and_alt_keys():
    apiaries = {"apiaries": [
        {"hives": [{"id": "HX", "positions": [{"id": "DX"}]}]},
    ]}
    hive_id, device_id = discover.walk_sample_ids(apiaries)
    assert hive_id == "HX"
    assert device_id == "DX"


def test_walk_sample_ids_empty():
    assert discover.walk_sample_ids([]) == (None, None)
    assert discover.walk_sample_ids([{"hives": []}]) == (None, None)
    assert discover.walk_sample_ids(None) == (None, None)


def test_sample_endpoint_success_stores_and_clips():
    out = {}
    discover.sample_endpoint(out, "hive_readings", "→ header", "hive readings",
                             lambda: {"a": 1}, clip=10000)
    assert out["hive_readings_sample"] == {"a": 1}
    assert "hive_readings_error" not in out


def test_sample_endpoint_error_stores_error():
    out = {}

    def boom():
        raise discover.BroodMinderError(500, "GET", "/x", "kaboom")

    discover.sample_endpoint(out, "device_readings", "→ header", "device readings",
                             boom, clip=100)
    assert "device_readings_sample" not in out
    assert "kaboom" in out["device_readings_error"]


# ==========================================================================
# extract_all.py
# ==========================================================================
def _args(**over):
    base = dict(no_notes=False, stop_after_empty=0, max_calls=900, reverse=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_bump_empty_off_is_noop():
    args = _args(stop_after_empty=0)
    assert extract_all._bump_empty(args, 3, 0) == 3
    assert extract_all._bump_empty(args, 3, 5) == 3


def test_bump_empty_counts_and_resets():
    args = _args(stop_after_empty=2)
    assert extract_all._bump_empty(args, 1, 0) == 2      # empty -> increment
    assert extract_all._bump_empty(args, 5, 10) == 0     # non-empty -> reset


def test_stop_predicate():
    assert extract_all._stop(_args(stop_after_empty=0), 99) is False
    assert extract_all._stop(_args(stop_after_empty=2), 1) is False
    assert extract_all._stop(_args(stop_after_empty=2), 2) is True


class _FakeBM:
    def __init__(self, readings, notes=None):
        self._readings = readings
        self._notes = notes if notes is not None else []
        self.call_count = 0

    def hive_readings(self, hid, s, e):
        self.call_count += 1
        return self._readings

    def hive_notes(self, hid, s, e):
        self.call_count += 1
        return self._notes


def test_fetch_window_writes_and_counts(tmp_path):
    readings = [{"positionID": "p", "readings": [{"timestamp": 1}, {"timestamp": 2}]}]
    notes = [{"description": "n"}]
    bm = _FakeBM(readings, notes)
    hdir = tmp_path / "H1"
    a = {"apiaryId": "A", "name": "Api"}
    h = {"hiveId": "H1", "name": "Hive"}
    rec = extract_all.fetch_window(bm, a, h, hdir, 0, 100, _args())
    assert rec["reading_rows"] == 2
    assert rec["notes"] == 1
    assert rec["apiaryId"] == "A"
    # files written, gz-readable
    with gzip.open(hdir / "0-100.readings.json.gz", "rt") as fh:
        r = json.load(fh)
    assert r == readings
    assert (hdir / "0-100.notes.json.gz").exists()


def test_fetch_window_no_notes(tmp_path):
    bm = _FakeBM([{"positionID": "p", "readings": []}])
    hdir = tmp_path / "H1"
    rec = extract_all.fetch_window(bm, {"apiaryId": "A", "name": "Api"},
                                   {"hiveId": "H1", "name": "Hive"}, hdir, 0, 100,
                                   _args(no_notes=True))
    assert "notes" not in rec
    assert not (hdir / "0-100.notes.json.gz").exists()


def test_process_hive_budget_raises_stopiteration(tmp_path):
    bm = _FakeBM([{"positionID": "p", "readings": [{"timestamp": 1}]}])
    bm.call_count = 900  # already at budget
    completed = {}
    with pytest.raises(StopIteration):
        extract_all.process_hive(bm, {"apiaryId": "A", "name": "Api"},
                                  {"hiveId": "H1", "name": "Hive"},
                                  [(0, 100)], tmp_path, completed,
                                  _args(max_calls=900), lambda: None)
    assert completed == {}  # nothing fetched


def test_process_hive_skips_completed(tmp_path):
    bm = _FakeBM([{"positionID": "p", "readings": [{"timestamp": 1}]}])
    completed = {"H1|0|100": {"reading_rows": 5}}
    extract_all.process_hive(bm, {"apiaryId": "A", "name": "Api"},
                             {"hiveId": "H1", "name": "Hive"},
                             [(0, 100)], tmp_path, completed,
                             _args(), lambda: None)
    assert bm.call_count == 0  # already-completed window not re-fetched


def test_process_hive_stop_after_empty(tmp_path):
    # Two empty windows; stop_after_empty=1 should stop after the first.
    bm = _FakeBM([{"positionID": "p", "readings": []}])
    completed = {}
    extract_all.process_hive(bm, {"apiaryId": "A", "name": "Api"},
                             {"hiveId": "H1", "name": "Hive"},
                             [(0, 100), (100, 200)], tmp_path, completed,
                             _args(stop_after_empty=1, no_notes=True), lambda: None)
    assert len(completed) == 1  # stopped after first empty window


# ==========================================================================
# flatten.py
# ==========================================================================
def _write_gz(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _make_raw(tmp_path):
    raw = tmp_path / "raw"
    # Hive H1: two windows, second overlaps the first (dup row) to test dedup.
    _write_gz(raw / "H1" / "0-100.readings.json.gz",
              [{"positionID": "p1", "readings": [
                  {"deviceId": "d1", "timestamp": 10, "batteryLevel": 90,
                   "chargeRemaining": 80, "readings": {"temp": 20.0, "hum": 50.0}},
                  {"deviceId": "d1", "timestamp": 20, "batteryLevel": None,
                   "chargeRemaining": None, "readings": {"temp": 21.0}},
              ]}])
    _write_gz(raw / "H1" / "100-200.readings.json.gz",
              [{"positionID": "p1", "readings": [
                  {"deviceId": "d1", "timestamp": 20, "batteryLevel": None,
                   "chargeRemaining": None, "readings": {"temp": 21.0}},  # dup
                  {"deviceId": "d1", "timestamp": 30, "batteryLevel": 88,
                   "chargeRemaining": 70, "readings": {"weight": 5.0}},
              ]}])
    return raw


def test_hive_dirs_only_dirs(tmp_path):
    raw = tmp_path / "raw"
    (raw / "H1").mkdir(parents=True)
    (raw / "loose.txt").parent.mkdir(exist_ok=True)
    (raw / "loose.txt").write_text("x")
    assert [d.name for d in flatten.hive_dirs(raw)] == ["H1"]


def test_discover_metric_keys(tmp_path):
    raw = _make_raw(tmp_path)
    keys = flatten.discover_metric_keys(raw)
    assert keys == {"temp", "hum", "weight"}


def test_stream_readings_dedups_and_covers(tmp_path):
    raw = _make_raw(tmp_path)
    meta = {"H1": {"apiaryId": "A", "apiaryName": "Api", "hiveName": "Hive"}}
    from collections import defaultdict
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    ndjson = tmp_path / "out.ndjson"
    with ndjson.open("w") as fh:
        n = flatten.stream_readings(raw, meta, [], [], fh, None, coverage)
    assert n == 3  # 4 rows, 1 duplicate removed
    lines = [json.loads(x) for x in ndjson.read_text().splitlines()]
    assert {ln["timestamp"] for ln in lines} == {10, 20, 30}
    assert lines[0]["m_temp"] == 20.0
    c = coverage["H1"]
    assert c["rows"] == 3
    assert c["min_ts"] == 10 and c["max_ts"] == 30
    assert c["devices"] == {"d1"} and c["positions"] == {"p1"}


def test_write_notes_list_and_dict(tmp_path):
    raw = tmp_path / "raw"
    _write_gz(raw / "H1" / "0-100.notes.json.gz", [{"description": "a"}])
    _write_gz(raw / "H2" / "0-100.notes.json.gz", {"notes": [{"description": "b"}]})
    meta = {"H1": {"hiveName": "One"}, "H2": {"hiveName": "Two"}}
    out = tmp_path / "notes.ndjson"
    n = flatten.write_notes(raw, meta, out)
    assert n == 2
    recs = [json.loads(x) for x in out.read_text().splitlines()]
    assert {r["hiveId"] for r in recs} == {"H1", "H2"}
    assert {r["description"] for r in recs} == {"a", "b"}


def test_build_coverage_out(tmp_path):
    coverage = {"H1": {"rows": 2, "min_ts": 10, "max_ts": 30,
                       "devices": {"d1", None}, "positions": {"p1"}}}
    meta = {"H1": {"apiaryName": "Api", "hiveName": "Hive"}}
    cov = flatten.build_coverage_out(coverage, meta)
    assert cov["H1"]["rows"] == 2
    assert cov["H1"]["devices"] == ["d1"]        # None filtered out
    assert cov["H1"]["earliest"].startswith("1970-01-01")
    assert cov["H1"]["hiveName"] == "Hive"
