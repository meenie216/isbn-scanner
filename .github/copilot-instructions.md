# Copilot Instructions

## Project Overview

ISBN Scanner is a mobile-friendly barcode scanning web app for cataloguing books, DVDs, Blu-rays, and CDs into named boxes at locations. Users scan barcodes via device camera; the system looks up metadata from public APIs and stores results in PostgreSQL.

## Architecture

**Frontend** (S3 + CloudFront): Vanilla JS/HTML5 — no framework. ~600 lines across 4 files. ZXing-js for barcode detection, Tesseract.js for OCR fallback.

**Backend** (AWS Lambda, Python 3.12): Six Lambda functions behind API Gateway (HTTP). Async lookup via SQS. Scheduled enrichment via EventBridge (every 5 min). Database is Neon PostgreSQL (serverless, pooled via PgBouncer).

**Async scan flow**: `POST /scan` → `scan_handler` inserts a `scan_record` (status=pending) + enqueues to SQS → `lookup_worker` calls external APIs + updates record (status=found/not_found/error) → frontend polls `GET /scan/{id}` until resolved.

**Enrichment flow**: `enrichment_worker` runs on EventBridge schedule, picks up un-enriched DVDs (TMDB) and CDs (MusicBrainz) in batches of 5, updates `enriched=true` even on not-found to avoid retry loops.

**Infrastructure**: Defined entirely in `template.yaml` (AWS SAM). Secrets stored in AWS SSM Parameter Store under `/isbn-scanner/`.

## Build & Deploy Commands

```bash
# Build Lambda packages + layer
sam build

# First deploy (interactive, sets samconfig.toml)
sam deploy --guided

# Subsequent deploys
sam deploy

# Local API (requires Docker + env.json with SSM values)
sam local start-api --env-vars env.json

# Serve frontend locally
cd frontend && python3 -m http.server 8080

# Deploy frontend to S3
aws s3 sync frontend/ s3://$BUCKET/ --delete
```

No test suite or lint tooling exists. Test manually via `sam local start-api` or curl against the deployed API.

## Backend Conventions

**Lambda handler shape**: Every handler returns `{"statusCode": int, "headers": {...}, "body": json.dumps(...)}`. CORS headers are set on every response.

**Database access**: Always use `get_conn()` from `backend/shared/db.py` — it reuses the global connection across warm invocations with a keep-alive ping. Use `psycopg2.extras.RealDictCursor` so rows come back as dicts.

**Parameterized queries**: Always use `%s` placeholders. The one exception is dynamic table names (e.g., `f"SELECT * FROM {item_table}"`), which are safe because `item_table` is an internal controlled value, never user input.

**Upserts**: Use `INSERT ... ON CONFLICT ... DO UPDATE` for idempotency (e.g., when reinserting a book/DVD already seen).

**Error handling in lookup_worker**: `LookupError` → set status `not_found`; any other `Exception` → set status `error`. Always use `ReportBatchItemFailures` for partial SQS batch failures.

**Barcode classification** (`shared/lookup.py`): ISBN-13 starts with 978/979; ISBN-10 is 10 digits. UPCs are classified by category string from UPC Item DB, with title keyword fallbacks ("dvd", "blu-ray" → dvd; " cd", "audio cd" → cd).

**Enrichment rate limits**: MusicBrainz requires 1 req/sec — always `time.sleep(1)` between requests. TMDB has no hard limit at this scale.

## Frontend Conventions

**Helpers** (defined in `app.js`): `$(sel, ctx)` = `querySelector`, `show(el)`/`hide(el)` toggle `.hidden`, `esc(s)` HTML-escapes strings — always use `esc()` before inserting user-visible data into the DOM.

**API calls**: Use the `apiFetch(path, opts)` wrapper — it prepends `API_BASE` (from `config.js`) and returns `{ok, status, data}`.

**Polling**: `setInterval` with `MAX_POLLS=30` at 2000ms intervals. Always clear the interval on success, failure, or page/tab change to avoid ghost polls.

**No state management library**: UI state lives in module-level variables. DOM is mutated directly — no virtual DOM, no data binding.

**config.js**: Contains only `window.API_BASE = "..."`. This file is rewritten at deploy time with the actual API Gateway URL — do not hardcode URLs elsewhere.

## Database Schema

Five tables in PostgreSQL:

- **`books`** — keyed on `isbn` (UNIQUE); columns include `authors[]`, `genres[]`
- **`dvds`** — keyed on `barcode` (UNIQUE); includes `media_format` (DVD|Blu-ray|4K UHD|VHS), `enriched` bool
- **`cds`** — keyed on `barcode` (UNIQUE); includes `track_listing[]`, `enriched` bool
- **`other_items`** — keyed on `barcode` (UNIQUE); fallback for unrecognized UPCs
- **`scan_records`** — one row per scan; links to item via `item_id` + `item_table`; status enum: `pending|found|not_found|error`

Schema source of truth is `sql/schema.sql`. There are no migrations — apply schema changes manually against Neon.

## External API Notes

| API | Used for | Key in SSM |
|-----|----------|-----------|
| Open Library | ISBN → book metadata | (no key required) |
| Google Books | ISBN fallback | `/isbn-scanner/google-books-api-key` |
| UPC Item DB | UPC → product (100/day free) | (no key required) |
| TMDB | DVD enrichment (director, cast, genres) | `/isbn-scanner/tmdb-api-key` |
| MusicBrainz | CD enrichment | (no key required) |
