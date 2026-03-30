"""
get_scan/app.py — GET /scan/{scan_id}

Returns the scan_record plus the full item details (from books or dvds)
once the lookup worker has completed.

Response shape:
  {
    "scan_id":   "...",
    "barcode":   "...",
    "status":    "pending" | "found" | "not_found" | "error",
    "media_type": "book" | "dvd" | "unknown" | null,
    "box_number": "...",
    "location":   "...",
    "notes":      "...",
    "scanned_at": "2024-...",
    "item": { ... }   // null if still pending or not found
  }
"""

import json

from shared.db import get_conn


def lambda_handler(event, context):
    path_params = event.get("pathParameters") or {}
    scan_id = path_params.get("scan_id", "").strip()
    if not scan_id:
        return _resp(400, {"error": "scan_id is required"})

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM scan_records WHERE id = %s",
                (scan_id,),
            )
            row = cur.fetchone()
    except Exception as e:
        return _resp(500, {"error": str(e)})

    if not row:
        return _resp(404, {"error": "Scan record not found"})

    result = {
        "scan_id":    str(row["id"]),
        "barcode":    row["barcode"],
        "status":     row["status"],
        "media_type": row["media_type"],
        "box_number": row["box_number"],
        "location":   row["location"],
        "notes":      row["notes"],
        "scanned_at": row["scanned_at"].isoformat() if row["scanned_at"] else None,
        "error_msg":  row["error_msg"],
        "item":       None,
    }

    if row["status"] == "found" and row["item_id"] and row["item_table"]:
        item = _fetch_item(conn, row["item_table"], row["item_id"])
        if item:
            result["item"] = item

    return _resp(200, result)


def _fetch_item(conn, table: str, item_id: str) -> dict | None:
    if table not in ("books", "dvds"):
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {table} WHERE id = %s", (str(item_id),))
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["id"] = str(d["id"])
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        return d
    except Exception:
        return None


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
