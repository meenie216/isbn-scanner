"""
retry_worker/app.py — EventBridge scheduled Lambda

Runs hourly. Picks up failed scan_records (status not_found or error) and
retries the lookup with the current lookup logic, so items self-heal when
a previously missing API source (e.g. Trove, Google Books) becomes available.

Retry policy:
  - Only retries records where retry_count < MAX_RETRIES (24)
  - Only retries records not attempted in the last hour
  - Processes at most BATCH_SIZE (20) records per run
  - On exhausting retries the record is left as-is; retry_count can be reset
    manually via the database to re-open dead scans
"""

from datetime import datetime, timezone

from shared.db import get_conn
from shared import lookup as lookup_lib

MAX_RETRIES = 24
BATCH_SIZE  = 20

_TABLE_MAP = {
    "book":  "books",
    "dvd":   "dvds",
    "cd":    "cds",
    "other": "other_items",
}


def lambda_handler(event, context):
    conn   = get_conn()
    rows   = _fetch_candidates(conn)
    found  = 0
    failed = 0

    for row in rows:
        try:
            _bump_retry(conn, row["id"])
            result     = lookup_lib.lookup(row["barcode"])
            media_type = result["media_type"]
            item_table = _TABLE_MAP.get(media_type, "other_items")
            item_id    = _upsert_item(conn, result)
            _update_scan(conn, row["id"], "found", media_type, item_id, item_table)
            found += 1
            print(f"retry OK: {row['barcode']} → {result.get('title', '?')}")
        except lookup_lib.LookupError as e:
            guessed = "book" if lookup_lib.is_book(row["barcode"]) else "dvd"
            _update_scan(conn, row["id"], "not_found", guessed, None, None, str(e))
            failed += 1
        except Exception as e:
            conn.rollback()
            _update_scan(conn, row["id"], "error", None, None, None, str(e))
            print(f"retry error: {row['barcode']}: {e}")
            failed += 1

    print(f"Retry worker done: {found} resolved, {failed} still failed, {len(rows)} processed")
    return {"resolved": found, "failed": failed, "processed": len(rows)}


def _fetch_candidates(conn) -> list:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, barcode
            FROM   scan_records
            WHERE  status IN ('not_found', 'error')
              AND  retry_count < %s
              AND  (last_retried_at IS NULL
                    OR last_retried_at < NOW() - INTERVAL '1 hour')
            ORDER BY scanned_at ASC
            LIMIT  %s
            """,
            (MAX_RETRIES, BATCH_SIZE),
        )
        return cur.fetchall()


def _bump_retry(conn, scan_id: str):
    """Increment retry_count and stamp last_retried_at before attempting lookup."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE scan_records
            SET    retry_count     = retry_count + 1,
                   last_retried_at = NOW()
            WHERE  id = %s
            """,
            (scan_id,),
        )
    conn.commit()


def _update_scan(conn, scan_id, status, media_type, item_id, item_table, error_msg=None):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scan_records
                SET    status     = %s,
                       media_type = COALESCE(%s, media_type),
                       item_id    = %s,
                       item_table = %s,
                       error_msg  = %s
                WHERE  id = %s
                """,
                (status, media_type, item_id, item_table, error_msg, scan_id),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Failed to update scan_record {scan_id}: {e}")


# ── Upsert helpers (mirrors lookup_worker) ────────────────────────────────────

def _upsert_item(conn, result: dict) -> str:
    dispatch = {
        "book":  _upsert_book,
        "dvd":   _upsert_dvd,
        "cd":    _upsert_cd,
        "other": _upsert_other,
    }
    return dispatch.get(result["media_type"], _upsert_other)(conn, result)


def _upsert_book(conn, r: dict) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO books
                (isbn, title, authors, publisher, published_year,
                 genres, language, pages, description, cover_url, source)
            VALUES
                (%(isbn)s, %(title)s, %(authors)s, %(publisher)s, %(published_year)s,
                 %(genres)s, %(language)s, %(pages)s, %(description)s, %(cover_url)s, %(source)s)
            ON CONFLICT (isbn) DO UPDATE SET
                title          = EXCLUDED.title,
                authors        = EXCLUDED.authors,
                publisher      = EXCLUDED.publisher,
                published_year = EXCLUDED.published_year,
                genres         = EXCLUDED.genres,
                language       = EXCLUDED.language,
                pages          = EXCLUDED.pages,
                description    = EXCLUDED.description,
                cover_url      = EXCLUDED.cover_url,
                source         = EXCLUDED.source
            RETURNING id
            """,
            r,
        )
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _upsert_dvd(conn, r: dict) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dvds
                (barcode, title, director, cast_members, studio, release_year,
                 genres, runtime_mins, rating, media_format, description, cover_url, source)
            VALUES
                (%(barcode)s, %(title)s, %(director)s, %(cast_members)s, %(studio)s,
                 %(release_year)s, %(genres)s, %(runtime_mins)s, %(rating)s,
                 %(media_format)s, %(description)s, %(cover_url)s, %(source)s)
            ON CONFLICT (barcode) DO UPDATE SET
                title        = EXCLUDED.title,
                director     = EXCLUDED.director,
                cast_members = EXCLUDED.cast_members,
                release_year = EXCLUDED.release_year,
                genres       = EXCLUDED.genres,
                runtime_mins = EXCLUDED.runtime_mins,
                rating       = EXCLUDED.rating,
                description  = EXCLUDED.description,
                cover_url    = EXCLUDED.cover_url,
                source       = EXCLUDED.source
            RETURNING id
            """,
            r,
        )
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _upsert_cd(conn, r: dict) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cds
                (barcode, title, artist, label, release_year,
                 genres, description, cover_url, source)
            VALUES
                (%(barcode)s, %(title)s, %(artist)s, %(label)s, %(release_year)s,
                 %(genres)s, %(description)s, %(cover_url)s, %(source)s)
            ON CONFLICT (barcode) DO UPDATE SET
                title        = EXCLUDED.title,
                artist       = EXCLUDED.artist,
                label        = EXCLUDED.label,
                release_year = EXCLUDED.release_year,
                genres       = EXCLUDED.genres,
                description  = EXCLUDED.description,
                cover_url    = EXCLUDED.cover_url,
                source       = EXCLUDED.source
            RETURNING id
            """,
            r,
        )
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _upsert_other(conn, r: dict) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO other_items
                (barcode, title, brand, category, description, cover_url, source)
            VALUES
                (%(barcode)s, %(title)s, %(brand)s, %(category)s,
                 %(description)s, %(cover_url)s, %(source)s)
            ON CONFLICT (barcode) DO UPDATE SET
                title       = EXCLUDED.title,
                brand       = EXCLUDED.brand,
                category    = EXCLUDED.category,
                description = EXCLUDED.description,
                cover_url   = EXCLUDED.cover_url,
                source      = EXCLUDED.source
            RETURNING id
            """,
            r,
        )
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id
