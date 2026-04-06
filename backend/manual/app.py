"""
manual/app.py — POST /manual

Manually resolves a failed scan_record by accepting user-supplied
item details, inserting into the appropriate item table, and updating
the scan_record status to 'found'.

Request body (JSON):
  scan_id     — UUID of the scan_record (required)
  media_type  — 'book' | 'dvd' | 'cd' | 'other' (required)
  title       — item title (required)
  authors     — comma-separated string (books only)
  director    — string (dvds only)
  artist      — string (cds only)
  brand       — string (other only)
  year        — integer release/publish year (optional)
  cover_url   — image URL (optional)
"""

import json

from shared.db import get_conn


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON body")

    scan_id    = (body.get("scan_id") or "").strip()
    media_type = (body.get("media_type") or "").strip().lower()
    title      = (body.get("title") or "").strip()

    if not scan_id:
        return _err(400, "scan_id is required")
    if media_type not in ("book", "dvd", "cd", "other"):
        return _err(400, "media_type must be book, dvd, cd, or other")
    if not title:
        return _err(400, "title is required")

    conn = get_conn()
    try:
        # Fetch the scan record to get the barcode
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, barcode, status FROM scan_records WHERE id = %s",
                (scan_id,),
            )
            scan = cur.fetchone()

        if not scan:
            return _err(404, "Scan record not found")

        barcode = scan["barcode"]

        year = body.get("year")
        if year is not None:
            try:
                year = int(year)
            except (ValueError, TypeError):
                year = None

        cover_url = (body.get("cover_url") or "").strip() or None

        if media_type == "book":
            authors_raw = (body.get("authors") or "").strip()
            authors = [a.strip() for a in authors_raw.split(",") if a.strip()] if authors_raw else []
            result = {
                "media_type":     "book",
                "isbn":           barcode,
                "title":          title,
                "authors":        authors,
                "publisher":      (body.get("publisher") or "").strip() or None,
                "published_year": year,
                "genres":         [],
                "language":       None,
                "pages":          None,
                "description":    None,
                "cover_url":      cover_url,
                "source":         "manual",
            }
            item_id    = _upsert_book(conn, result)
            item_table = "books"

        elif media_type == "dvd":
            result = {
                "media_type":   "dvd",
                "barcode":      barcode,
                "title":        title,
                "director":     (body.get("director") or "").strip() or None,
                "cast_members": [],
                "studio":       (body.get("studio") or "").strip() or None,
                "release_year": year,
                "genres":       [],
                "runtime_mins": None,
                "rating":       None,
                "media_format": (body.get("media_format") or "DVD").strip(),
                "description":  None,
                "cover_url":    cover_url,
                "source":       "manual",
            }
            item_id    = _upsert_dvd(conn, result)
            item_table = "dvds"

        elif media_type == "cd":
            result = {
                "media_type":   "cd",
                "barcode":      barcode,
                "title":        title,
                "artist":       (body.get("artist") or "").strip() or None,
                "label":        (body.get("label") or "").strip() or None,
                "release_year": year,
                "genres":       [],
                "description":  None,
                "cover_url":    cover_url,
                "source":       "manual",
            }
            item_id    = _upsert_cd(conn, result)
            item_table = "cds"

        else:  # other
            result = {
                "media_type":  "other",
                "barcode":     barcode,
                "title":       title,
                "brand":       (body.get("brand") or "").strip() or None,
                "category":    (body.get("category") or "").strip() or None,
                "description": None,
                "cover_url":   cover_url,
                "source":      "manual",
            }
            item_id    = _upsert_other(conn, result)
            item_table = "other_items"

        _update_scan(conn, scan_id, media_type, item_id, item_table)

    finally:
        conn.close()

    return {
        "statusCode": 200,
        "headers":    {"Content-Type": "application/json",
                       "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"ok": True, "item_id": item_id}),
    }


def _err(status, msg):
    return {
        "statusCode": status,
        "headers":    {"Content-Type": "application/json",
                       "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"error": msg}),
    }


def _upsert_book(conn, r):
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
            cover_url      = EXCLUDED.cover_url,
            source         = EXCLUDED.source
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, r)
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _upsert_dvd(conn, r):
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
            release_year = EXCLUDED.release_year,
            media_format = EXCLUDED.media_format,
            cover_url    = EXCLUDED.cover_url,
            source       = EXCLUDED.source
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, r)
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _upsert_cd(conn, r):
    sql = """
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
            cover_url    = EXCLUDED.cover_url,
            source       = EXCLUDED.source
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, r)
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _upsert_other(conn, r):
    sql = """
        INSERT INTO other_items
            (barcode, title, brand, category, description, cover_url, source)
        VALUES
            (%(barcode)s, %(title)s, %(brand)s, %(category)s,
             %(description)s, %(cover_url)s, %(source)s)
        ON CONFLICT (barcode) DO UPDATE SET
            title    = EXCLUDED.title,
            brand    = EXCLUDED.brand,
            category = EXCLUDED.category,
            cover_url = EXCLUDED.cover_url,
            source   = EXCLUDED.source
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, r)
        item_id = str(cur.fetchone()["id"])
    conn.commit()
    return item_id


def _update_scan(conn, scan_id, media_type, item_id, item_table):
    """Resolve all scan_records sharing the same barcode, not just the one submitted."""
    with conn.cursor() as cur:
        # Get the barcode from the submitted scan record
        cur.execute("SELECT barcode FROM scan_records WHERE id = %s", (scan_id,))
        row = cur.fetchone()
        if not row:
            return
        barcode = row["barcode"]

        cur.execute(
            """
            UPDATE scan_records
            SET    status     = 'found',
                   media_type = %s,
                   item_id    = %s,
                   item_table = %s,
                   error_msg  = NULL
            WHERE  barcode = %s
              AND  status IN ('not_found', 'error')
            """,
            (media_type, item_id, item_table, barcode),
        )
    conn.commit()
