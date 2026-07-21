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
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "extract"
RAW = OUT / "raw"

from bm.helpers import (  # noqa: E402
    build_row,
    discover_metric_keys,
    hive_dirs,
    iter_rows,
    load_json,
)


def hive_meta(manifest: dict) -> dict:
    meta = {}
    for key, rec in manifest.get("completed", {}).items():
        hid = key.split("|", 1)[0]
        meta.setdefault(hid, {"apiaryName": rec.get("apiaryName"),
                              "apiaryId": rec.get("apiaryId"),
                              "hiveName": rec.get("hiveName")})
    return meta


def iter_note_files(hdir: Path):
    yield from sorted(list(hdir.glob("*.notes.json")) + list(hdir.glob("*.notes.json.gz")))


def _update_coverage(c: dict, r: dict, pid, ts) -> None:
    """Fold one reading into a hive's running coverage stats."""
    c["rows"] += 1
    c["devices"].add(r.get("deviceId"))
    c["positions"].add(pid)
    if ts:
        c["min_ts"] = ts if c["min_ts"] is None else min(c["min_ts"], ts)
        c["max_ts"] = ts if c["max_ts"] is None else max(c["max_ts"], ts)


def stream_readings(raw_root, meta, base_cols, metric_cols, ndjson_fh, csv_writer):
    """Pass 2: stream every de-duplicated reading row to ndjson (+ optional csv),
    accumulating per-hive coverage. Returns (n_rows, coverage)."""
    coverage = defaultdict(lambda: {"rows": 0, "min_ts": None, "max_ts": None,
                                    "devices": set(), "positions": set()})
    n_rows = 0
    for hdir in hive_dirs(raw_root):
        hid = hdir.name
        m = meta.get(hid, {})
        # Dedupe within a hive: overlapping/re-fetched windows can repeat the
        # same (position, device, timestamp). Reset per hive to bound memory.
        seen: set = set()
        for pid, r in iter_rows(hdir):
            ts = r.get("timestamp")
            dk = (pid, r.get("deviceId"), ts)
            if dk in seen:
                continue
            seen.add(dk)
            row = build_row(m, hid, pid, r)
            ndjson_fh.write(json.dumps(row) + "\n")
            if csv_writer:
                csv_writer.writerow({k: row.get(k) for k in base_cols + metric_cols})
            n_rows += 1
            _update_coverage(coverage[hid], r, pid, ts)
    return n_rows, coverage


def write_notes(out_path: Path, raw_root: Path, meta: dict) -> int:
    """Write all hive notes to a plain ndjson file. Returns the note count."""
    n_notes = 0
    with out_path.open("w") as fh:
        for hdir in hive_dirs(raw_root):
            hid = hdir.name
            m = meta.get(hid, {})
            for f in iter_note_files(hdir):
                payload = load_json(f)
                items = payload if isinstance(payload, list) else (payload or {}).get("notes", [])
                for n in items or []:
                    fh.write(json.dumps({"hiveId": hid, "hiveName": m.get("hiveName"), **n}) + "\n")
                    n_notes += 1
    return n_notes


def build_coverage_output(coverage: dict, meta: dict) -> dict:
    """Render the accumulated per-hive coverage stats into the output shape."""
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

    # Pass 1: discover metric keys (stable, tiny set) so CSV has a fixed header.
    metric_keys = discover_metric_keys(RAW)
    metric_cols = [f"m_{k}" for k in sorted(metric_keys)]

    # Pass 2: stream rows to gzipped ndjson (+ optional gzipped csv).
    ndjson_fh = gzip.open(OUT / "readings.ndjson.gz", "wt", encoding="utf-8")
    csv_fh = csv_writer = None
    if not args.no_csv:
        csv_fh = io.TextIOWrapper(gzip.open(OUT / "readings.csv.gz", "wb"), encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_fh, fieldnames=base_cols + metric_cols)
        csv_writer.writeheader()

    try:
        n_rows, coverage = stream_readings(RAW, meta, base_cols, metric_cols, ndjson_fh, csv_writer)
    finally:
        ndjson_fh.close()
        if csv_fh:
            csv_fh.close()

    # Notes (small) -> plain ndjson
    n_notes = write_notes(OUT / "notes.ndjson", RAW, meta)

    cov_out = build_coverage_output(coverage, meta)
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
