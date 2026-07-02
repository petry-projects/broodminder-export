# broodminder-export

**Get *all* of your data out of the [BroodMinder](https://broodminder.com) cloud — apiaries, hives, devices, every sensor reading, and notes — into plain, portable files you own.**

`broodminder-export` is a small, dependency-light Python tool that talks to the
BroodMinder External User API and pulls your complete history into gzipped JSON,
NDJSON, and CSV. It is **resumable** and **rate-limit-aware**, so even large
multi-apiary accounts with years of history can be extracted reliably within the
API's daily quota.

> ⚠️ **Unofficial.** This project is not affiliated with or endorsed by
> BroodMinder. It uses the public External User API with your own API key. The
> bundled [OpenAPI spec](openapi/broodminder-openapi.yaml) is reverse-engineered
> from observed behavior — corrections welcome.

## Why

The BroodMinder app and web app are great, but your data lives in their cloud.
If you want to run your own analytics, build dashboards, train models, or simply
keep an archive you control, you need a clean export. That's all this does.

## What you get

A flattened, analysis-ready row per reading:

| field | notes |
|---|---|
| `apiaryId`, `apiaryName`, `hiveId`, `hiveName` | account topology |
| `positionID`, `deviceId` | sensor identity (`deviceId` is the unique series key) |
| `timestamp`, `datetime` | Unix epoch seconds (UTC) + ISO string |
| `batteryLevel`, `chargeRemaining` | device power (nullable; they alternate) |
| `m_temperature`, `m_humidity`, `m_weight`, `m_swarmState` | sensor metrics (presence varies by device) |

Outputs (under `data/extract/`):
`readings.ndjson.gz`, `readings.csv.gz`, `notes.ndjson`, `coverage.json`
(per-hive earliest/latest + counts), plus the lossless raw `.json.gz` windows.

## Get an API key

The External User API is in alpha. Request a key from BroodMinder
(support@broodminder.com). The key is tied to your account and only authorizes
access to your own data.

## Quick start

```bash
git clone https://github.com/petry-projects/broodminder-export.git
cd broodminder-export

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # then paste your key into .env
```

`.env`:
```
BROODMINDER_API_KEY=your-api-key-here
BROODMINDER_BASE_URL=https://external-api.mybroodminder.com
```

Then:

```bash
# 1. Confirm auth + see your account topology
.venv/bin/python scripts/discover.py

# 2. Pull recent history (resumable, stops before the daily cap)
.venv/bin/python scripts/extract_all.py --start 2025-01-01

# 3. Build analysis-ready NDJSON + CSV (no API calls)
.venv/bin/python scripts/flatten.py
```

## Full-history extraction

The API is capped at **~1000 calls per day**. A small account finishes in one
run; a large one (many hives, several years) spans a few days. The extractor is
**resumable** — just run it again and it skips windows already fetched
(`data/extract/manifest.json`).

To efficiently walk *backwards* to the beginning of your data — skipping hives
that have no old data — use backfill mode:

```bash
.venv/bin/python scripts/extract_all.py \
    --start 2016-01-01 --end 2025-01-01 \
    --reverse --stop-after-empty 3
```

For a fully unattended multi-day pull, [`scripts/cron_backfill.sh`](scripts/cron_backfill.sh)
runs the resumable backfill on a schedule (idempotent and safe to repeat):

```bash
( crontab -l 2>/dev/null; \
  echo "20 */6 * * * $(pwd)/scripts/cron_backfill.sh" ) | crontab -
```

## Key behaviors of the API (observed)

The full, machine-readable description is in
[`openapi/broodminder-openapi.yaml`](openapi/broodminder-openapi.yaml). Highlights
this tool handles for you:

- **Auth header** `X-Api-Key`. A missing/invalid key returns **HTTP 412** (not
  the usual 401/403).
- **6-month window cap** per readings/notes request — the tool chunks any range.
- **No pagination**: within a window the whole result is one JSON array (can be
  multiple MB). No delta endpoint, so the tool de-duplicates overlapping windows.
- **Daily rate limit** (~1000/day, body `"daily limit exceeded"`), no
  `Retry-After` header — the tool self-throttles and resumes.

## Contract tests

A live test suite validates the endpoints and pins the API's real behavior
(useful as a canary when the API changes). It runs only when a key is present:

```bash
.venv/bin/python -m pytest        # skips automatically without BROODMINDER_API_KEY
```

## Your data stays yours

`.env` (your key) and `data/` (your hive data) are git-ignored and never leave
your machine. This repo contains code only.

## Contributing & security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Please
**never** paste an API key or raw hive data into an issue or PR.

## License

[MIT](LICENSE) © Petry Projects.
