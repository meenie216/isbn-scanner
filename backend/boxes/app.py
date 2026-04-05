"""
boxes/app.py — GET /boxes

Returns distinct boxes with item counts, ordered by location then box number.
"""

import json
from shared.db import get_conn


def lambda_handler(event, context):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(box_number, '(no box)') AS box_number,
                    location,
                    COUNT(*)                          AS item_count,
                    MAX(scanned_at)                   AS last_scanned
                FROM scan_records
                GROUP BY box_number, location
                ORDER BY location NULLS LAST, box_number NULLS LAST
            """)
            rows = cur.fetchall()
    except Exception as e:
        return _resp(500, {"error": str(e)})

    boxes = [
        {
            "box_number":   r["box_number"],
            "location":     r["location"] or "",
            "item_count":   r["item_count"],
            "last_scanned": r["last_scanned"].isoformat() if r["last_scanned"] else None,
        }
        for r in rows
    ]

    return _resp(200, {"boxes": boxes})


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
