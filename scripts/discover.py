"""Discovery: confirm auth, dump the real account topology + sample schemas.

Run this FIRST. It:
  1. Calls /user/metadata/apiaries and prints the apiary/hive tree.
  2. Pulls a small recent readings/notes sample for the first hive + device so
     we can see the *actual* field names (the docs are approximate).
  3. Writes the raw JSON to data/discovery.json for the tests to key off.

Stays well under the rate limit (a handful of calls).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bm.client import BroodMinderClient, now_epoch  # noqa: E402
from bm.helpers import find_sample_ids, first, sample_endpoint  # noqa: E402

DAY = 24 * 60 * 60
OUT = Path(__file__).resolve().parent.parent / "data" / "discovery.json"


def main() -> int:
    out: dict = {}
    with BroodMinderClient() as bm:
        print(f"base_url = {bm.base_url}")
        print("→ GET /user/metadata/apiaries")
        try:
            apiaries = bm.apiaries()
        except BroodMinderError as e:
            print(f"AUTH/METADATA FAILED: {e}", file=sys.stderr)
            return 1
        out["apiaries"] = apiaries
        print(json.dumps(apiaries, indent=2)[:4000])

        # Walk the tree to find a hive id and a device id to sample.
        containers = apiaries if isinstance(apiaries, list) else apiaries.get("apiaries", [])
        hive_id, device_id = find_sample_ids(containers)
        print(f"\nsample hive_id={hive_id}  device_id={device_id}")

        end = now_epoch()
        start = end - 30 * DAY  # last 30 days as a probe
        if hive_id is not None:
            sample_endpoint(out, "hive_readings",
                            f"→ GET /user/hive/{hive_id}/readings (last 30d)",
                            lambda: bm.hive_readings(hive_id, start, end), 2500)
            sample_endpoint(out, "hive_notes",
                            f"→ GET /user/hive/{hive_id}/notes (last 30d)",
                            lambda: bm.hive_notes(hive_id, start, end), 1500)

        if device_id is not None:
            sample_endpoint(out, "device_readings",
                            f"→ GET /user/device/{device_id}/readings (last 30d)",
                            lambda: bm.device_readings(device_id, start, end), 2500)

        out["_call_count"] = bm.call_count
        print(f"\ntotal API calls this run: {bm.call_count}")

    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
