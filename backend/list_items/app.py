"""
list_items/app.py — GET /items

Returns a paginated list of scan_records with resolved item details.

Query parameters:
  location  — filter by location (case-insensitive prefix match)
  box       — filter by box_number (exact)
  status    — filter by scan status (default: all)
  page      — page number, 1-based (default: 1)
  page_size — items per page (default: 20, max: 100)
"""

import json
import math

from shared.db import get_conn

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


def lambda_handler(event, context):
    qs = event.get("queryStringParameters") or {}

    location  = (qs.get("location") or "").strip() or None
    box       = (qs.get("box")      or "").strip() or None
    status    = (qs.get("status")   or "all").strip()

    try:
        page      = max(1, int(qs.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(qs.get("page_size", DEFAULT_PAGE_SIZE))))
    except (ValueError, TypeError):
        page, page_size = 1, DEFAULT_PAGE_SIZE

    offset = (page - 1) * page_size

    filters = []
    params  = []

    if status != "all":
        filters.append("s.status = %s")
        params.append(status)

    if location:
        filters.append("s.location ILIKE %s")
        params.append(f"{location}%")
    if box:
        filters.append("s.box_number = %s")
        params.append(box)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM scan_records s {where}", params)
            total = cur.fetchone()["n"]

            # Explicit column list avoids duplicate-name overwrite when LEFT JOINing
            # both books and dvds (they share column names like id, title, created_at).
            cur.execute(
                f"""
                SELECT
                    s.id          AS scan_id,
                    s.barcode,
                    s.box_number,
                    s.location,
                    s.notes,
                    s.media_type,
                    s.status      AS scan_status,
                    s.error_msg,
                    s.scanned_at,
                    b.isbn,
                    b.title       AS book_title,
                    b.authors,
                    b.publisher,
                    b.published_year,
                    b.genres      AS book_genres,
                    b.cover_url   AS book_cover_url,
                    d.title       AS dvd_title,
                    d.director,
                    d.release_year,
                    d.genres      AS dvd_genres,
                    d.rating,
                    d.cover_url   AS dvd_cover_url
                FROM scan_records s
                LEFT JOIN books b ON s.item_table = 'books' AND s.item_id = b.id
                LEFT JOIN dvds  d ON s.item_table = 'dvds'  AND s.item_id = d.id
                {where}
                ORDER BY s.scanned_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            rows = cur.fetchall()
    except Exception as e:
        conn.rollback()
        return _resp(500, {"error": str(e)})

    items = [_format_row(r) for r in rows]

    return _resp(200, {
        "items":       items,
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": math.ceil(total / page_size) if total else 0,
    })


def _format_row(row) -> dict:
    r = dict(row)
    media_type = r.get("media_type")

    base = {
        "scan_id":    str(r["scan_id"]),
        "barcode":    r.get("barcode"),
        "box_number": r.get("box_number"),
        "location":   r.get("location"),
        "notes":      r.get("notes"),
        "media_type": media_type,
        "status":     r.get("scan_status"),
        "error_msg":  r.get("error_msg"),
        "scanned_at": r["scanned_at"].isoformat() if r.get("scanned_at") else None,
        "item":       None,
    }

    if media_type == "book" and r.get("isbn"):
        base["item"] = {
            "type":           "book",
            "title":          r.get("book_title"),
            "authors":        r.get("authors") or [],
            "publisher":      r.get("publisher"),
            "published_year": r.get("published_year"),
            "genres":         r.get("book_genres") or [],
            "cover_url":      r.get("book_cover_url"),
        }
    elif media_type == "dvd" and r.get("dvd_title"):
        base["item"] = {
            "type":         "dvd",
            "title":        r.get("dvd_title"),
            "director":     r.get("director"),
            "release_year": r.get("release_year"),
            "genres":       r.get("dvd_genres") or [],
            "rating":       r.get("rating"),
            "cover_url":    r.get("dvd_cover_url"),
        }

    return base


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
