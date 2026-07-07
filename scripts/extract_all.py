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


class BudgetReached(Exception):
    """Raised to unwind out of the hive loop once the call budget is hit."""


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


def select_hives(apiaries, apiary_filters):
    """Flatten apiaries into [(apiary, hive)], optionally filtered by name/id."""
    if apiary_filters:
        wanted = {a.lower() for a in apiary_filters}
        apiaries = [a for a in apiaries
                    if a.get("name", "").lower() in wanted or a.get("apiaryId") in apiary_filters]
    return [(a, h) for a in apiaries for h in a.get("hives", [])]


def _empty_streak(reading_rows, streak, limit):
    """Track consecutive empty windows for --stop-after-empty backfill early-exit.

    Returns the updated streak and whether the stop limit has been reached.
    With limit falsy the feature is off: the streak is unchanged and never stops.
    """
    if not limit:
        return streak, False
    streak = streak + 1 if reading_rows == 0 else 0
    return streak, streak >= limit


def print_window(a, h, s, e, rec) -> None:
    ds = datetime.fromtimestamp(s, tz=timezone.utc)
    de = datetime.fromtimestamp(e, tz=timezone.utc)
    print(f"  {h['name']:>10} [{a['name'][:14]:<14}] "
          f"{ds:%Y-%m-%d}..{de:%Y-%m-%d}  "
          f"rows={rec['reading_rows']:<5} notes={rec.get('notes', '-')}")


def fetch_window(bm, hdir: Path, a, h, s, e, no_notes) -> dict:
    """Fetch one window's readings (+notes), write them to disk, return the record."""
    hdir.mkdir(parents=True, exist_ok=True)
    rec = {"apiaryId": a.get("apiaryId"), "apiaryName": a.get("name"),
           "hiveName": h.get("name")}
    readings = bm.hive_readings(h["hiveId"], s, e)
    write_gz(hdir / f"{s}-{e}.readings.json.gz", readings)
    rec["reading_rows"] = count_reading_rows(readings)
    if not no_notes:
        notes = bm.hive_notes(h["hiveId"], s, e)
        write_gz(hdir / f"{s}-{e}.notes.json.gz", notes)
        rec["notes"] = count_notes(notes)
    return rec


def _note_window(a, h, s, e, rec, completed, save_manifest) -> None:
    """Print progress for a non-empty window and checkpoint the manifest periodically."""
    if rec["reading_rows"] or rec.get("notes"):
        print_window(a, h, s, e, rec)
    if len(completed) % 25 == 0:
        save_manifest()


def process_hive(bm, a, h, wins, completed, args, raw, save_manifest) -> None:
    """Walk one hive's windows, fetching + recording each. Skips completed windows,
    honors --stop-after-empty, and raises BudgetReached when the call cap is hit."""
    hid = h["hiveId"]
    hdir = raw / hid
    consecutive_empty = 0
    for s, e in wins:
        key = f"{hid}|{s}|{e}"
        if key in completed:
            # Honor early-exit using cached row counts too, so a resumed
            # backfill doesn't walk past the known data edge.
            consecutive_empty, stop = _empty_streak(
                completed[key].get("reading_rows", 0), consecutive_empty, args.stop_after_empty)
            if stop:
                return
            continue
        if bm.call_count >= args.max_calls:
            print(f"\n⏸  budget reached ({bm.call_count} calls). Resume later.")
            raise BudgetReached
        rec = fetch_window(bm, hdir, a, h, s, e, args.no_notes)
        completed[key] = rec
        _note_window(a, h, s, e, rec, completed, save_manifest)
        # Early-exit bookkeeping for backfill: stop walking a hive backwards once
        # we hit a run of empty windows (data is contiguous; nothing older to find).
        consecutive_empty, stop = _empty_streak(
            rec["reading_rows"], consecutive_empty, args.stop_after_empty)
        if stop:
            return  # next hive


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
            hives = select_hives(apiaries, args.apiary)
            print(f"scope: {len(apiaries)} apiaries, {len(hives)} hives")
            print(f"range: {args.start} .. {args.end or 'now'}  "
                  f"({len(list(iter_windows(start, end, window)))} windows/hive)")
            print(f"budget: stop at {args.max_calls} calls (already used {bm.call_count})\n")

            for a, h in hives:
                wins = list(iter_windows(start, end, window))
                if args.reverse:
                    wins.reverse()
                process_hive(bm, a, h, wins, completed, args, raw, save_manifest)
        except BudgetReached:
            stopped_early = True
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
