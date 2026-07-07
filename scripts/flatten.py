"""Flatten raw extracted windows into analysis-ready outputs. No API calls.

Reads data/extract/raw/<hiveId>/*.readings.json[.gz] (+ notes) and produces:
    data/extract/readings.ndjson.gz   one JSON object per reading row (gzipped)
    data/extract/readings.csv.gz      same, columnar (gzipped; --no-csv to skip)
    data/extract/notes.ndjson         one object per note
    data/extract/coverage.json        per-hive earliest/latest ts + row counts

Streams rows straight to disk (no 1.4M-row list in memory) and compresses
output (the spike disk is small). Idempotent: rebuilds outputs each run, so
it's safe to re-run after every incremental extract.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "extract"
RAW = OUT / "raw"


def load_json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text())


def hive_meta(manifest: dict) -> dict:
    meta = {}
    for key, rec in manifest.get("completed", {}).items():
        hid = key.split("|", 1)[0]
        meta.setdefault(hid, {"apiaryName": rec.get("apiaryName"),
                              "apiaryId": rec.get("apiaryId"),
                              "hiveName": rec.get("hiveName")})
    return meta


def iter_reading_files(hdir: Path):
    """All readings windows for a hive, gz or plain."""
    yield from sorted(list(hdir.glob("*.readings.json")) + list(hdir.glob("*.readings.json.gz")))


def iter_note_files(hdir: Path):
    yield from sorted(list(hdir.glob("*.notes.json")) + list(hdir.glob("*.notes.json.gz")))


def _iter_position_readings(hdir: Path):
    """Yield (positionID, reading) for every reading in a hive dir's windows."""
    for f in iter_reading_files(hdir):
        for pos in load_json(f) or []:
            pid = pos.get("positionID")
            for r in pos.get("readings", []) or []:
                yield pid, r


def discover_metric_keys(raw_root: Path) -> set:
    """Pass 1: collect the (stable, tiny) set of metric keys for a fixed header."""
    metric_keys: set = set()
    for hdir in sorted(raw_root.iterdir()):
        if not hdir.is_dir():
            continue
        for _pid, r in _iter_position_readings(hdir):
            metric_keys.update((r.get("readings") or {}).keys())
    return metric_keys


def iter_dedup_readings(raw_root: Path, meta: dict):
    """Yield (hid, hive_meta, positionID, reading) for every reading row.

    Dedupes (position, device, timestamp) within a hive: overlapping/re-fetched
    windows can repeat rows. The seen-set resets per hive to bound memory.
    """
    for hdir in sorted(raw_root.iterdir()):
        if not hdir.is_dir():
            continue
        hid = hdir.name
        m = meta.get(hid, {})
        seen: set = set()
        for pid, r in _iter_position_readings(hdir):
            dk = (pid, r.get("deviceId"), r.get("timestamp"))
            if dk in seen:
                continue
            seen.add(dk)
            yield hid, m, pid, r


def build_row(hid: str, m: dict, pid, r: dict) -> dict:
    """Build one flat reading row, prefixing metric keys with ``m_``."""
    ts = r.get("timestamp")
    metrics = r.get("readings") or {}
    return {
        "apiaryId": m.get("apiaryId"), "apiaryName": m.get("apiaryName"),
        "hiveId": hid, "hiveName": m.get("hiveName"),
        "positionID": pid, "deviceId": r.get("deviceId"),
        "timestamp": ts,
        "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
        "batteryLevel": r.get("batteryLevel"),
        "chargeRemaining": r.get("chargeRemaining"),
        **{f"m_{k}": v for k, v in metrics.items()},
    }


def _update_coverage(c: dict, pid, r: dict) -> None:
    ts = r.get("timestamp")
    c["rows"] += 1
    c["devices"].add(r.get("deviceId"))
    c["positions"].add(pid)
    if ts:
        c["min_ts"] = ts if c["min_ts"] is None else min(c["min_ts"], ts)
        c["max_ts"] = ts if c["max_ts"] is None else max(c["max_ts"], ts)


def stream_readings(raw_root: Path, meta: dict, base_cols, metric_cols, no_csv: bool, out: Path):
    """Pass 2: stream deduped rows to gzipped ndjson (+ optional gzipped csv).

    Returns (n_rows, coverage). Rows are written straight to disk (no big list
    in memory).
    """
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    n_rows = 0
    ndjson_fh = gzip.open(out / "readings.ndjson.gz", "wt", encoding="utf-8")
    csv_fh = csv_writer = None
    if not no_csv:
        csv_fh = io.TextIOWrapper(gzip.open(out / "readings.csv.gz", "wb"), encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_fh, fieldnames=base_cols + metric_cols)
        csv_writer.writeheader()

    try:
        for hid, m, pid, r in iter_dedup_readings(raw_root, meta):
            row = build_row(hid, m, pid, r)
            ndjson_fh.write(json.dumps(row) + "\n")
            if csv_writer:
                csv_writer.writerow({k: row.get(k) for k in base_cols + metric_cols})
            n_rows += 1
            _update_coverage(coverage[hid], pid, r)
    finally:
        ndjson_fh.close()
        if csv_fh:
            csv_fh.close()
    return n_rows, coverage


def _iter_notes(hdir: Path):
    """Yield each note dict from a hive dir (payload is a bare list or {notes:[...]})."""
    for f in iter_note_files(hdir):
        payload = load_json(f)
        items = payload if isinstance(payload, list) else (payload or {}).get("notes", [])
        yield from items or []


def write_notes(raw_root: Path, meta: dict, out: Path) -> int:
    """Flatten notes (small) to plain ndjson. Returns the note count."""
    n_notes = 0
    with (out / "notes.ndjson").open("w") as fh:
        for hdir in sorted(raw_root.iterdir()):
            if not hdir.is_dir():
                continue
            hid = hdir.name
            m = meta.get(hid, {})
            for n in _iter_notes(hdir):
                fh.write(json.dumps({"hiveId": hid, "hiveName": m.get("hiveName"), **n}) + "\n")
                n_notes += 1
    return n_notes


def build_coverage(coverage: dict, meta: dict) -> dict:
    """Turn the per-hive coverage accumulator into the JSON-serializable summary."""
    cov_out = {}
    for hid, c in coverage.items():
        m = meta.get(hid, {})
        cov_out[hid] = {
            "apiaryName": m.get("apiaryName"), "hiveName": m.get("hiveName"),
            "rows": c["rows"],
            "devices": sorted(d for d in c["devices"] if d),
            "positions": sorted(p for p in c["positions"] if p),
            "earliest": datetime.fromtimestamp(c["min_ts"], tz=timezone.utc).isoformat() if c["min_ts"] else None,
            "latest": datetime.fromtimestamp(c["max_ts"], tz=timezone.utc).isoformat() if c["max_ts"] else None,
        }
    return cov_out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-csv", action="store_true", help="skip the (large) CSV output")
    args = ap.parse_args()

    if not RAW.exists():
        print("no raw data yet; run extract_all.py first", file=sys.stderr)
        return 1

    manifest = load_json(OUT / "manifest.json") if (OUT / "manifest.json").exists() else {}
    meta = hive_meta(manifest)

    base_cols = ["apiaryId", "apiaryName", "hiveId", "hiveName", "positionID",
                 "deviceId", "timestamp", "datetime", "batteryLevel", "chargeRemaining"]

    metric_keys = discover_metric_keys(RAW)
    metric_cols = [f"m_{k}" for k in sorted(metric_keys)]

    n_rows, coverage = stream_readings(RAW, meta, base_cols, metric_cols, args.no_csv, OUT)
    n_notes = write_notes(RAW, meta, OUT)

    cov_out = build_coverage(coverage, meta)
    (OUT / "coverage.json").write_text(json.dumps(cov_out, indent=2))

    print(f"readings rows : {n_rows}")
    print(f"notes         : {n_notes}")
    print(f"metrics seen  : {sorted(metric_keys)}")
    print(f"hives w/ data : {len(cov_out)}")
    outs = "readings.ndjson.gz, " + ("" if args.no_csv else "readings.csv.gz, ") + "notes.ndjson, coverage.json"
    print(f"wrote: {outs} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
