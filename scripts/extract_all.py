"""Resumable, budget-aware full-history extraction of all BroodMinder data.

Walks every apiary -> hive, windows the full time range past the API's 6-month
per-request cap, and pulls readings (+ notes) for each window. Writes the raw
responses to disk losslessly and records progress in a manifest so the run can
be stopped and resumed across days (the key is capped at 1000 calls/day).

Layout (under --out, default data/extract/):
    manifest.json                         progress + per-window row counts
    raw/<hiveId>/<start>-<end>.readings.json
    raw/<hiveId>/<start>-<end>.notes.json

Re-run with the same args to resume; completed windows are skipped. Use
scripts/flatten.py afterwards to build analysis-ready NDJSON/CSV (no API calls).

Examples:
    python scripts/extract_all.py --start 2022-01-01 --max-calls 800
    python scripts/extract_all.py --apiary "My Apiary" --start 2024-06-01
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bm.client import (  # noqa: E402
    BroodMinderClient,
    BroodMinderError,
    RateLimited,
    iter_windows,
    now_epoch,
)


def write_gz(path: Path, obj) -> None:
    """Write a JSON object gzip-compressed (raw hive data is highly repetitive
    and the spike disk is small — gzip shrinks it ~19x)."""
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(obj, fh)


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


def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"completed": {}, "meta": {}}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2021-01-01", help="history start (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="history end (YYYY-MM-DD); default now")
    p.add_argument("--window-days", type=int, default=180, help="<= ~6 months")
    p.add_argument("--apiary", action="append", default=[],
                   help="filter by apiary name or id (repeatable); default all")
    p.add_argument("--max-calls", type=int, default=900,
                   help="stop before this many API calls (1000/day cap)")
    p.add_argument("--out", default=str(ROOT / "data" / "extract"))
    p.add_argument("--no-notes", action="store_true", help="skip notes endpoint")
    p.add_argument("--reverse", action="store_true",
                   help="walk windows newest->oldest (for backfilling history)")
    p.add_argument("--stop-after-empty", type=int, default=0,
                   help="with --reverse: stop a hive after N consecutive empty "
                        "windows (saves calls on hives with no old data; 0=off)")
    return p


def select_hives(apiaries, filters):
    """Apply the optional --apiary filter and flatten to (apiary, hive) pairs."""
    if filters:
        wanted = {a.lower() for a in filters}
        apiaries = [a for a in apiaries
                    if a.get("name", "").lower() in wanted or a.get("apiaryId") in filters]
    hives = [(a, h) for a in apiaries for h in a.get("hives", [])]
    return apiaries, hives


def _advance_streak(rows: int, args, streak: int):
    """Advance the consecutive-empty-window streak from a window's row count.

    Returns ``(streak, should_stop)``. A no-op passthrough (never stops) when
    the --stop-after-empty backfill early-exit is disabled.
    """
    if not args.stop_after_empty:
        return streak, False
    streak = streak + 1 if rows == 0 else 0
    return streak, streak >= args.stop_after_empty


def _fetch_window(bm, a, h, hid, hdir, s, e, args) -> dict:
    """Fetch (and persist) one window's readings + notes; return its record."""
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


def _emit_progress(a, h, s, e, rec) -> None:
    ds = datetime.fromtimestamp(s, tz=timezone.utc)
    de = datetime.fromtimestamp(e, tz=timezone.utc)
    print(f"  {h['name']:>10} [{a['name'][:14]:<14}] "
          f"{ds:%Y-%m-%d}..{de:%Y-%m-%d}  "
          f"rows={rec['reading_rows']:<5} notes={rec.get('notes', '-')}")


def _process_window(bm, a, h, hid, hdir, s, e, key, args, completed, save_manifest) -> dict:
    """Fetch a fresh window, record it, print progress, checkpoint periodically."""
    rec = _fetch_window(bm, a, h, hid, hdir, s, e, args)
    completed[key] = rec
    if rec["reading_rows"] or rec.get("notes"):
        _emit_progress(a, h, s, e, rec)
    if len(completed) % 25 == 0:
        save_manifest()
    return rec


def extract_hive(bm, a, h, raw, completed, args, start, end, window, save_manifest) -> bool:
    """Walk one hive's windows, fetching the ones not already completed.

    Returns True if the API-call budget was reached (caller should stop the
    whole run). Skips completed windows and honors the backfill empty-streak
    early exit (--stop-after-empty), using cached row counts on resume so a
    resumed backfill doesn't walk past the known data edge.
    """
    hid = h["hiveId"]
    hdir = raw / hid
    wins = list(iter_windows(start, end, window))
    if args.reverse:
        wins.reverse()
    streak = 0
    for s, e in wins:
        key = f"{hid}|{s}|{e}"
        if key in completed:
            streak, stop = _advance_streak(completed[key].get("reading_rows", 0), args, streak)
            if stop:
                break
            continue
        if bm.call_count >= args.max_calls:
            print(f"\n⏸  budget reached ({bm.call_count} calls). Resume later.")
            return True
        rec = _process_window(bm, a, h, hid, hdir, s, e, key, args, completed, save_manifest)
        streak, stop = _advance_streak(rec["reading_rows"], args, streak)
        if stop:
            break  # next hive
    return False


def main() -> int:
    args = build_parser().parse_args()

    start = parse_date(args.start)
    # Snap the open end to midnight UTC so re-runs within a day reuse the same
    # window key (stable resume; the live now-epoch would otherwise mint a new
    # key each run and re-fetch the final window). Today's partial data is
    # picked up on the next day's run.
    if args.end:
        end = parse_date(args.end)
    else:
        end = now_epoch() // 86400 * 86400
    window = args.window_days * 24 * 60 * 60
    out = Path(args.out)
    raw = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = load_manifest(manifest_path)
    completed: dict = manifest["completed"]

    def save_manifest():
        manifest["meta"] = {
            "start": start, "end": end, "window_days": args.window_days,
            "windows_completed": len(completed),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))

    stopped_early = False
    with BroodMinderClient() as bm:
        try:
            # The very first call can itself be rate-limited; keep it inside the
            # handler so a 429 here exits cleanly (resumable) rather than crashing.
            apiaries = bm.apiaries()
            apiaries, hives = select_hives(apiaries, args.apiary)
            print(f"scope: {len(apiaries)} apiaries, {len(hives)} hives")
            print(f"range: {args.start} .. {args.end or 'now'}  "
                  f"({len(list(iter_windows(start, end, window)))} windows/hive)")
            print(f"budget: stop at {args.max_calls} calls (already used {bm.call_count})\n")

            for a, h in hives:
                if extract_hive(bm, a, h, raw, completed, args, start, end, window, save_manifest):
                    stopped_early = True
                    break
        except RateLimited as ex:
            print(f"\n⏸  rate limited by server ({ex.status}). Saving and exiting; resume later.")
            stopped_early = True
        except BroodMinderError as ex:
            save_manifest()
            print(f"\n✗ API error: {ex}", file=sys.stderr)
            return 2

        save_manifest()
        total_rows = sum(v.get("reading_rows", 0) for v in completed.values())
        total_notes = sum(v.get("notes", 0) for v in completed.values())
        print(f"\n{'paused' if stopped_early else 'done'}: "
              f"{len(completed)} windows, {total_rows} reading rows, {total_notes} notes")
        print(f"API calls this run: {bm.call_count}")
        print(f"raw -> {raw}\nmanifest -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
