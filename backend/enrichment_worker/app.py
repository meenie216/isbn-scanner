"""
enrichment_worker/app.py — EventBridge scheduled Lambda

Runs every 5 minutes. Picks up un-enriched DVDs and CDs and augments
them with library-quality metadata from TMDB and MusicBrainz.

Processing rate is intentionally low (5 items per type per run) to:
  - Respect MusicBrainz 1 req/sec rate limit
  - Stay well within Lambda free tier
  - Avoid hammering external APIs
"""

import time

from shared.db import get_conn
from shared import enrich as enrich_lib


BATCH_SIZE = 5  # items per type per run


def lambda_handler(event, context):
    conn = get_conn()
    dvd_done = _enrich_dvds(conn)
    cd_done  = _enrich_cds(conn)
    print(f"Enriched: {dvd_done} DVDs, {cd_done} CDs")
    return {"dvds_enriched": dvd_done, "cds_enriched": cd_done}


def _enrich_dvds(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, barcode FROM dvds WHERE enriched = false ORDER BY created_at LIMIT %s",
            (BATCH_SIZE,),
        )
        rows = cur.fetchall()

    enriched = 0
    for row in rows:
        try:
            data = enrich_lib.enrich_dvd(row["title"], row["barcode"])
            if data:
                _update_dvd(conn, row["id"], data)
                enriched += 1
            else:
                # Mark as enriched even if nothing found — avoids retrying forever
                _mark_enriched(conn, "dvds", row["id"])
        except Exception as e:
            print(f"DVD enrich error {row['id']}: {e}")
            _mark_enriched(conn, "dvds", row["id"])

    return enriched


def _enrich_cds(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, barcode, title, artist FROM cds WHERE enriched = false ORDER BY created_at LIMIT %s",
            (BATCH_SIZE,),
        )
        rows = cur.fetchall()

    enriched = 0
    for row in rows:
        try:
            data = enrich_lib.enrich_cd(row["barcode"], row["title"], row["artist"])
            if data:
                _update_cd(conn, row["id"], data)
                enriched += 1
            else:
                _mark_enriched(conn, "cds", row["id"])
        except Exception as e:
            print(f"CD enrich error {row['id']}: {e}")
            _mark_enriched(conn, "cds", row["id"])
        finally:
            time.sleep(1)  # MusicBrainz rate limit between requests

    return enriched


def _update_dvd(conn, item_id: str, data: dict):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE dvds SET
                director     = COALESCE(%s, director),
                cast_members = CASE WHEN %s != '{}' THEN %s ELSE cast_members END,
                genres       = CASE WHEN %s != '{}' THEN %s ELSE genres END,
                runtime_mins = COALESCE(%s, runtime_mins),
                rating       = COALESCE(%s, rating),
                release_year = COALESCE(%s, release_year),
                description  = COALESCE(%s, description),
                cover_url    = COALESCE(%s, cover_url),
                source       = %s,
                enriched     = true
            WHERE id = %s
            """,
            (
                data.get("director"),
                data.get("cast_members", []),
                data.get("cast_members", []),
                data.get("genres", []),
                data.get("genres", []),
                data.get("runtime_mins"),
                data.get("rating"),
                data.get("release_year"),
                data.get("description"),
                data.get("cover_url"),
                data.get("enrich_source", "tmdb"),
                str(item_id),
            ),
        )
    conn.commit()


def _update_cd(conn, item_id: str, data: dict):
    tracks = data.get("track_listing") or []
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE cds SET
                artist        = COALESCE(%s, artist),
                label         = COALESCE(%s, label),
                release_year  = COALESCE(%s, release_year),
                genres        = CASE WHEN %s != '{}' THEN %s ELSE genres END,
                track_listing = CASE WHEN %s != '{}' THEN %s ELSE track_listing END,
                cover_url     = COALESCE(%s, cover_url),
                source        = %s,
                enriched      = true
            WHERE id = %s
            """,
            (
                data.get("artist"),
                data.get("label"),
                data.get("release_year"),
                tracks, tracks,
                tracks, tracks,
                data.get("cover_url"),
                data.get("enrich_source", "musicbrainz"),
                str(item_id),
            ),
        )
    conn.commit()


def _mark_enriched(conn, table: str, item_id: str):
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE {table} SET enriched = true WHERE id = %s", (str(item_id),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Failed to mark {table} {item_id} as enriched: {e}")
