"""
scan_handler/app.py — POST /scan

Accepts a JSON body:
  {
    "barcode":    "9780140328721",
    "box_number": "B12",          (optional)
    "location":   "Garage",       (optional)
    "notes":      "..."           (optional)
  }

1. Validates input
2. Inserts a scan_record (status=pending) into Neon
3. Publishes the scan_id + barcode to SQS for async lookup
4. Returns { "scan_id": "<uuid>" }
"""

import json
import os
import uuid
import boto3

from shared.db import get_conn

sqs = boto3.client("sqs")
QUEUE_URL = os.environ["SCAN_QUEUE_URL"]


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "Invalid JSON"})

    barcode = (body.get("barcode") or "").strip()
    if not barcode:
        return _resp(400, {"error": "barcode is required"})

    box_number = (body.get("box_number") or "").strip() or None
    location   = (body.get("location")   or "").strip() or None
    notes      = (body.get("notes")      or "").strip() or None

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scan_records
                    (barcode, box_number, location, notes, status)
                VALUES (%s, %s, %s, %s, 'pending')
                RETURNING id
                """,
                (barcode, box_number, location, notes),
            )
            scan_id = str(cur.fetchone()["id"])
        conn.commit()
    except Exception as e:
        conn.rollback()
        return _resp(500, {"error": f"Database error: {e}"})

    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps({"scan_id": scan_id, "barcode": barcode}),
    )

    return _resp(202, {"scan_id": scan_id})


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
