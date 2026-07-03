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

from bm.client import BroodMinderClient, BroodMinderError, now_epoch  # noqa: E402

DAY = 24 * 60 * 60
OUT = Path(__file__).resolve().parent.parent / "data" / "discovery.json"


def first(obj, *keys):
    """Return the first present key from a dict (schemas vary)."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                return obj[k]
    return None


def find_sample_ids(apiaries):
    """Walk the apiary tree and return the first (hive_id, device_id) found.

    Tolerant of the alpha API's schema drift: apiaries may be a bare list or a
    ``{"apiaries": [...]}`` wrapper, and ids appear under varying key names.
    """
    hive_id = device_id = None
    containers = apiaries if isinstance(apiaries, list) else apiaries.get("apiaries", [])
    for ap in containers or []:
        for hv in first(ap, "hives") or []:
            hive_id = hive_id or first(hv, "hiveId", "id", "hiveID")
            for dv in (first(hv, "devices", "positions") or []):
                device_id = device_id or first(dv, "deviceId", "id", "deviceID")
    return hive_id, device_id


def _sample_endpoint(out, noun, label, fetch, sample_key, error_key, clip):
    """Call one sample endpoint, storing the result or the error in ``out``.

    Keeps ``main()`` flat: the readings/notes probes are all the same shape
    (announce → call → record → print), differing only in labels and the
    bound ``fetch`` closure.
    """
    print(f"→ {label}")
    try:
        payload = fetch()
    except BroodMinderError as e:
        out[error_key] = str(e)
        print(f"  {noun} error: {e}")
        return
    out[sample_key] = payload
    print(json.dumps(payload, indent=2)[:clip])


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

        hive_id, device_id = find_sample_ids(apiaries)
        print(f"\nsample hive_id={hive_id}  device_id={device_id}")

        end = now_epoch()
        start = end - 30 * DAY  # last 30 days as a probe
        if hive_id is not None:
            _sample_endpoint(out, "hive readings",
                             f"GET /user/hive/{hive_id}/readings (last 30d)",
                             lambda: bm.hive_readings(hive_id, start, end),
                             "hive_readings_sample", "hive_readings_error", 2500)
            _sample_endpoint(out, "hive notes",
                             f"GET /user/hive/{hive_id}/notes (last 30d)",
                             lambda: bm.hive_notes(hive_id, start, end),
                             "hive_notes_sample", "hive_notes_error", 1500)

        if device_id is not None:
            _sample_endpoint(out, "device readings",
                             f"GET /user/device/{device_id}/readings (last 30d)",
                             lambda: bm.device_readings(device_id, start, end),
                             "device_readings_sample", "device_readings_error", 2500)

        out["_call_count"] = bm.call_count
        print(f"\ntotal API calls this run: {bm.call_count}")

    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
