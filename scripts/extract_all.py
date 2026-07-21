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
import json
import sys
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
from bm.helpers import (  # noqa: E402
    _BudgetReached,
    filter_apiaries,
    parse_date,
    walk_hive,
)


def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"completed": {}, "meta": {}}


def main() -> int:
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
    args = p.parse_args()

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
            apiaries = filter_apiaries(apiaries, args.apiary)
            hives = [(a, h) for a in apiaries for h in a.get("hives", [])]
            print(f"scope: {len(apiaries)} apiaries, {len(hives)} hives")
            print(f"range: {args.start} .. {args.end or 'now'}  "
                  f"({len(list(iter_windows(start, end, window)))} windows/hive)")
            print(f"budget: stop at {args.max_calls} calls (already used {bm.call_count})\n")

            for a, h in hives:
                wins = list(iter_windows(start, end, window))
                if args.reverse:
                    wins.reverse()
                walk_hive(bm, a, h, wins, completed, args, raw, save_manifest)
        except _BudgetReached:
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
