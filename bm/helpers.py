"""Reusable helpers shared across CLI scripts.

Import from here instead of from scripts/. Entry points (scripts/) import
these functions; tests pin them directly via `from bm.helpers import ...`.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from bm.client import BroodMinderError


# ── discover helpers ──────────────────────────────────────────────────────────

def first(obj, *keys):
    """Return the first present key from a dict (schemas vary)."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
    return None


def find_sample_ids(containers):
    """Walk the apiary -> hive -> device tree and return the first
    (hive_id, device_id) we can find to sample. Either may be None."""
    hive_id = device_id = None
    for ap in containers or []:
        for hv in first(ap, "hives") or []:
            if hive_id is None:
                hive_id = first(hv, "hiveId", "id", "hiveID")
            if device_id is None:
                for dv in first(hv, "devices", "positions") or []:
                    device_id = first(dv, "deviceId", "id", "deviceID")
                    if device_id is not None:
                        break
            if hive_id is not None and device_id is not None:
                return hive_id, device_id
    return hive_id, device_id


def sample_endpoint(out, key, header, fetch, preview):
    """Run one discovery fetch: print `header`, then record the result under
    `<key>_sample` (previewed) or the error under `<key>_error`. Errors are
    captured, not raised, so a single failing endpoint doesn't abort discovery."""
    print(header)
    try:
        data = fetch()
    except BroodMinderError as e:
        out[f"{key}_error"] = str(e)
        print(f"  {key.replace('_', ' ')} error: {e}")
        return
    out[f"{key}_sample"] = data
    print(json.dumps(data, indent=2)[:preview])


# ── extract_all helpers ───────────────────────────────────────────────────────

def parse_date(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def count_reading_rows(payload) -> int:
    if not isinstance(payload, list):
        return 0
    return sum(len(pos.get("readings", []) or []) for pos in payload)


def count_notes(payload) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return len(payload.get("notes", []) or [])
    return 0


def filter_apiaries(apiaries, apiary_filters):
    """Keep only apiaries whose name (case-insensitive) or id matches a filter.
    An empty filter list returns the apiaries unchanged."""
    if not apiary_filters:
        return apiaries
    wanted = {a.lower() for a in apiary_filters}
    return [a for a in apiaries
            if a.get("name", "").lower() in wanted or a.get("apiaryId") in apiary_filters]


def _empty_break(reading_rows, consecutive_empty, threshold):
    """Track consecutive-empty windows for --reverse backfill early-exit.
    Returns (consecutive_empty, should_break); threshold=0 disables the check."""
    if not threshold:
        return consecutive_empty, False
    consecutive_empty = consecutive_empty + 1 if reading_rows == 0 else 0
    return consecutive_empty, consecutive_empty >= threshold


class _BudgetReached(Exception):
    """Raised to unwind out of the hive walk once the call budget is hit."""


def write_gz(path: Path, obj) -> None:
    """Write a JSON object gzip-compressed."""
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)


def process_window(bm, a, h, hid, hdir, s, e, args) -> dict:
    """Fetch and persist one window's readings (+ notes), returning its
    manifest record with row/note counts."""
    hdir.mkdir(parents=True, exist_ok=True)
    rec = {"apiaryId": a.get("apiaryId"), "apiaryName": a.get("name"),
           "hiveName": h.get("name")}
    readings = bm.hive_readings(hid, s, e)
    write_gz(hdir / f"{s}-{e}.readings.json.gz", readings)
    rec["reading_rows"] = count_reading_rows(readings)

    if not args.no_notes:
        notes = bm.hive_notes(hid, s, e)
        write_gz(hdir / f"{s}-{e}.notes.json.gz", notes)
        rec["notes"] = count_notes(notes)
    return rec


def log_window(a, h, s, e, rec) -> None:
    """Print a one-line progress row for a non-empty window."""
    if rec["reading_rows"] or rec.get("notes"):
        ds = datetime.fromtimestamp(s, tz=timezone.utc)
        de = datetime.fromtimestamp(e, tz=timezone.utc)
        print(f"  {h['name']:>10} [{a['name'][:14]:<14}] "
              f"{ds:%Y-%m-%d}..{de:%Y-%m-%d}  "
              f"rows={rec['reading_rows']:<5} notes={rec.get('notes', '-')}")


def walk_hive(bm, a, h, wins, completed, args, raw, save_manifest) -> None:
    """Walk one hive's windows: skip completed ones, fetch the rest until the
    call budget is reached (raises _BudgetReached) or the backfill early-exit
    trips on a run of empty windows."""
    hid = h["hiveId"]
    hdir = raw / hid
    consecutive_empty = 0
    for s, e in wins:
        key = f"{hid}|{s}|{e}"
        if key in completed:
            # Honor early-exit using cached row counts too, so a resumed
            # backfill doesn't walk past the known data edge.
            consecutive_empty, stop = _empty_break(
                completed[key].get("reading_rows", 0), consecutive_empty, args.stop_after_empty)
            if stop:
                break
            continue
        if bm.call_count >= args.max_calls:
            print(f"\n⏸  budget reached ({bm.call_count} calls). Resume later.")
            raise _BudgetReached

        rec = process_window(bm, a, h, hid, hdir, s, e, args)
        completed[key] = rec
        # Early-exit bookkeeping for backfill: stop walking a hive backwards
        # once we hit a run of empty windows (data is effectively contiguous;
        # nothing older to find).
        consecutive_empty, stop = _empty_break(
            rec["reading_rows"], consecutive_empty, args.stop_after_empty)
        log_window(a, h, s, e, rec)
        if len(completed) % 25 == 0:
            save_manifest()
        if stop:
            break  # next hive


# ── flatten helpers ───────────────────────────────────────────────────────────

def load_json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text())


def iter_reading_files(hdir: Path):
    """All readings windows for a hive, gz or plain."""
    yield from sorted(list(hdir.glob("*.readings.json")) + list(hdir.glob("*.readings.json.gz")))


def hive_dirs(raw_root: Path):
    """Sorted hive subdirectories of the raw extract root."""
    return [d for d in sorted(raw_root.iterdir()) if d.is_dir()]


def iter_rows(hdir: Path):
    """Yield (positionID, reading) for every readings row under a hive dir."""
    for f in iter_reading_files(hdir):
        for pos in load_json(f) or []:
            pid = pos.get("positionID")
            for r in pos.get("readings", []) or []:
                yield pid, r


def discover_metric_keys(raw_root: Path) -> set:
    """Pass 1: scan every readings row to collect the (small, stable) set of
    metric keys, so the CSV can have a fixed header."""
    metric_keys: set[str] = set()
    for hdir in hive_dirs(raw_root):
        for _pid, r in iter_rows(hdir):
            metric_keys.update((r.get("readings") or {}).keys())
    return metric_keys


def build_row(m: dict, hid: str, pid, r: dict) -> dict:
    """Build one flat output row from a hive's meta and a single reading."""
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
