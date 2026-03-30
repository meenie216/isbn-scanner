"""
Database connection helper.

Reads the Neon PostgreSQL connection URL from:
  - AWS SSM Parameter Store  /isbn-scanner/db-url  (production)
  - Environment variable     DB_URL                 (local dev / testing)

Uses Neon's pooled (PgBouncer) connection endpoint to handle multiple
concurrent Lambda invocations without exhausting the connection limit.
"""

import os
import psycopg2
import psycopg2.extras
import boto3
import json

_conn = None


def _get_connection_url() -> str:
    url = os.environ.get("DB_URL")
    if url:
        return url

    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
    param = ssm.get_parameter(
        Name="/isbn-scanner/db-url",
        WithDecryption=True,
    )
    return param["Parameter"]["Value"]


def get_conn():
    """Return a live psycopg2 connection, reusing across warm Lambda invocations."""
    global _conn
    if _conn is None or _conn.closed:
        url = _get_connection_url()
        _conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        _conn.autocommit = False
    else:
        # Keep the connection healthy across warm invocations
        try:
            _conn.cursor().execute("SELECT 1")
        except Exception:
            _conn = None
            return get_conn()
    return _conn
