"""
list_failed/app.py — GET /failed

Returns paginated scan_records with status not_found or error,
ordered by most recently scanned. Used to drive the manual resolution UI.

Query parameters:
  page      — 1-based page number (default: 1)
  page_size — items per page (default: 20, max: 100)
"""

import json
import math

from shared.db import get_conn

MAX_PAGE_SIZE    = 100
DEFAULT_PAGE_SIZE = 20


def lambda_handler(event, context):
    qs        = event.get("queryStringParameters") or {}
    page      = max(1, int(qs.get("page", 1) or 1))
    page_size = min(MAX_PAGE_SIZE, max(1, int(qs.get("page_size", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)))
    offset    = (page - 1) * page_size

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM   scan_records
                WHERE  status IN ('not_found', 'error')
                """,
            )
            total = cur.fetchone()["total"]

            cur.execute(
                """
                SELECT id          AS scan_id,
                       barcode,
                       media_type,
                       box_number,
                       location,
                       notes,
                       status,
                       error_msg,
                       retry_count,
                       last_retried_at,
                       scanned_at
                FROM   scan_records
                WHERE  status IN ('not_found', 'error')
                ORDER  BY scanned_at DESC
                LIMIT  %s OFFSET %s
                """,
                (page_size, offset),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    items = []
    for r in rows:
        items.append({
            "scan_id":         str(r["scan_id"]),
            "barcode":         r["barcode"],
            "media_type":      r["media_type"],
            "box_number":      r["box_number"],
            "location":        r["location"],
            "notes":           r["notes"],
            "status":          r["status"],
            "error_msg":       r["error_msg"],
            "retry_count":     r["retry_count"] or 0,
            "scanned_at":      r["scanned_at"].isoformat() if r["scanned_at"] else None,
        })

    return {
        "statusCode": 200,
        "headers":    {"Content-Type": "application/json",
                       "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({
            "items":       items,
            "total":       total,
            "page":        page,
            "page_size":   page_size,
            "total_pages": math.ceil(total / page_size) if total else 1,
        }),
    }
