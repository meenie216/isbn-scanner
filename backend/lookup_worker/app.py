"""
lookup_worker/app.py — SQS trigger

For each SQS message:
  1. Calls shared/lookup.py with the barcode
  2. Upserts the result into books or dvds
  3. Updates scan_record with status=found / not_found / error and item_id

Uses ReportBatchItemFailures so a single failed message is re-queued
without discarding other messages in the batch.
"""

import json

from shared.db import get_conn
from shared import lookup as lookup_lib


def lambda_handler(event, context):
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            scan_id = body["scan_id"]
            barcode = body["barcode"]
            _process(scan_id, barcode)
        except Exception as e:
            print(f"ERROR processing message {message_id}: {e}")
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def _process(scan_id: str, barcode: str):
    conn = get_conn()
    try:
        result = lookup_lib.lookup(barcode)
        media_type = result["media_type"]
        item_id = _upsert_item(conn, result)
        _update_scan(conn, scan_id, "found", media_type, item_id,
                     "books" if media_type == "book" else "dvds")
    except lookup_lib.LookupError as e:
        _update_scan(conn, scan_id, "not_found",
                     "book" if lookup_lib.is_book(barcode) else "dvd",
                     None, None, str(e))
    except Exception as e:
        conn.rollback()
        _update_scan(conn, scan_id, "error", None, None, None, str(e))
        raise


def _upsert_item(conn, result: dict) -> str:
    if result["media_type"] == "book":
        return _upsert_book(conn, result)
    else:
        return _upsert_dvd(conn, result)


def _upsert_book(conn, r: dict) -> str:
    sql = """
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
    """
    with conn.cursor() as cur:
        cur.execute(sql, r)
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _upsert_dvd(conn, r: dict) -> str:
    sql = """
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
    """
    with conn.cursor() as cur:
        cur.execute(sql, r)
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _update_scan(conn, scan_id, status, media_type, item_id, item_table,
                 error_msg=None):
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE scan_records
                SET    status = %s,
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
