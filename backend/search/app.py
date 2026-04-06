"""
search/app.py — GET /search

Full-text search across the entire catalogue.

Query parameters:
  q         — search term (min 2 chars)
  page      — 1-based page number (default: 1)
  page_size — items per page (default: 20, max: 50)

Searches:
  books       — title, authors
  dvds        — title
  cds         — title, artist
  other_items — title, brand

Only returns scan_records with status='found'.
"""

import json
import math

from shared.db import get_conn

MAX_PAGE_SIZE     = 50
DEFAULT_PAGE_SIZE = 20
MIN_QUERY_LEN     = 2


def lambda_handler(event, context):
    qs = event.get("queryStringParameters") or {}

    q = (qs.get("q") or "").strip()
    if len(q) < MIN_QUERY_LEN:
        return _resp(400, {"error": f"Query must be at least {MIN_QUERY_LEN} characters"})

    try:
        page      = max(1, int(qs.get("page", 1)))
        page_size = min(MAX_PAGE_SIZE, max(1, int(qs.get("page_size", DEFAULT_PAGE_SIZE))))
    except (ValueError, TypeError):
        page, page_size = 1, DEFAULT_PAGE_SIZE

    offset = (page - 1) * page_size
    term   = f"%{q}%"

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM   scan_records s
                LEFT JOIN books       b ON s.item_table = 'books'       AND s.item_id = b.id
                LEFT JOIN dvds        d ON s.item_table = 'dvds'        AND s.item_id = d.id
                LEFT JOIN cds         c ON s.item_table = 'cds'         AND s.item_id = c.id
                LEFT JOIN other_items o ON s.item_table = 'other_items' AND s.item_id = o.id
                WHERE s.status = 'found'
                  AND (
                      b.title   ILIKE %s OR b.authors::text ILIKE %s
                   OR d.title   ILIKE %s
                   OR c.title   ILIKE %s OR c.artist       ILIKE %s
                   OR o.title   ILIKE %s OR o.brand        ILIKE %s
                  )
                """,
                [term] * 7,
            )
            total = cur.fetchone()["n"]

            cur.execute(
                """
                SELECT
                    s.id          AS scan_id,
                    s.barcode,
                    s.box_number,
                    s.location,
                    s.media_type,
                    s.scanned_at,
                    -- book columns
                    b.title       AS book_title,
                    b.authors,
                    b.publisher,
                    b.published_year,
                    b.cover_url   AS book_cover_url,
                    -- dvd columns
                    d.title       AS dvd_title,
                    d.director,
                    d.release_year AS dvd_release_year,
                    d.media_format,
                    d.cover_url   AS dvd_cover_url,
                    -- cd columns
                    c.title       AS cd_title,
                    c.artist,
                    c.label,
                    c.cover_url   AS cd_cover_url,
                    -- other columns
                    o.title       AS other_title,
                    o.brand,
                    o.cover_url   AS other_cover_url
                FROM scan_records s
                LEFT JOIN books       b ON s.item_table = 'books'       AND s.item_id = b.id
                LEFT JOIN dvds        d ON s.item_table = 'dvds'        AND s.item_id = d.id
                LEFT JOIN cds         c ON s.item_table = 'cds'         AND s.item_id = c.id
                LEFT JOIN other_items o ON s.item_table = 'other_items' AND s.item_id = o.id
                WHERE s.status = 'found'
                  AND (
                      b.title   ILIKE %s OR b.authors::text ILIKE %s
                   OR d.title   ILIKE %s
                   OR c.title   ILIKE %s OR c.artist       ILIKE %s
                   OR o.title   ILIKE %s OR o.brand        ILIKE %s
                  )
                ORDER BY s.scanned_at DESC
                LIMIT %s OFFSET %s
                """,
                [term] * 7 + [page_size, offset],
            )
            rows = cur.fetchall()
    except Exception as e:
        conn.rollback()
        return _resp(500, {"error": str(e)})

    return _resp(200, {
        "items":       [_format_row(r) for r in rows],
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": math.ceil(total / page_size) if total else 0,
        "query":       q,
    })


def _format_row(row) -> dict:
    r          = dict(row)
    media_type = r.get("media_type")
    item       = None

    if media_type == "book" and r.get("book_title"):
        item = {
            "type":           "book",
            "title":          r["book_title"],
            "authors":        r.get("authors") or [],
            "publisher":      r.get("publisher"),
            "published_year": r.get("published_year"),
            "cover_url":      r.get("book_cover_url"),
        }
    elif media_type == "dvd" and r.get("dvd_title"):
        item = {
            "type":         "dvd",
            "title":        r["dvd_title"],
            "director":     r.get("director"),
            "release_year": r.get("dvd_release_year"),
            "media_format": r.get("media_format"),
            "cover_url":    r.get("dvd_cover_url"),
        }
    elif media_type == "cd" and r.get("cd_title"):
        item = {
            "type":     "cd",
            "title":    r["cd_title"],
            "artist":   r.get("artist"),
            "label":    r.get("label"),
            "cover_url": r.get("cd_cover_url"),
        }
    elif r.get("other_title"):
        item = {
            "type":     "other",
            "title":    r["other_title"],
            "brand":    r.get("brand"),
            "cover_url": r.get("other_cover_url"),
        }

    return {
        "scan_id":    str(r["scan_id"]),
        "barcode":    r.get("barcode"),
        "box_number": r.get("box_number"),
        "location":   r.get("location"),
        "media_type": media_type,
        "status":     "found",
        "scanned_at": r["scanned_at"].isoformat() if r.get("scanned_at") else None,
        "item":       item,
    }


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"Content-Type": "application/json"},
        "body":       json.dumps(body, default=str),
    }
