"""
delete_scan/app.py — DELETE /scan/{scan_id}

Deletes a scan_record by ID.  If the referenced item in books/dvds/cds/other_items
is no longer referenced by any other scan_record, it is also deleted (orphan cleanup).
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
            # Fetch before deleting so we know the item to check for orphan cleanup
            cur.execute(
                "SELECT item_table, item_id FROM scan_records WHERE id = %s",
                (scan_id,),
            )
            row = cur.fetchone()
            if not row:
                return _resp(404, {"error": "Scan record not found"})

            item_table = row["item_table"]
            item_id    = row["item_id"]

            cur.execute("DELETE FROM scan_records WHERE id = %s", (scan_id,))

            # Clean up orphaned item row if nothing else references it
            if item_table and item_id and item_table in ("books", "dvds", "cds", "other_items"):
                cur.execute(
                    "SELECT COUNT(*) AS n FROM scan_records WHERE item_table = %s AND item_id = %s",
                    (item_table, str(item_id)),
                )
                if cur.fetchone()["n"] == 0:
                    cur.execute(f"DELETE FROM {item_table} WHERE id = %s", (str(item_id),))

        conn.commit()
    except Exception as e:
        conn.rollback()
        return _resp(500, {"error": str(e)})

    return _resp(200, {"deleted": scan_id})


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
