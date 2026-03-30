-- ISBN Scanner schema
-- Run once against your Neon database to initialise tables.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ──────────────────────────────────────────────────────────────────────────────
-- Books (populated from Open Library / Google Books)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS books (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    isbn           TEXT        UNIQUE NOT NULL,
    title          TEXT        NOT NULL,
    authors        TEXT[]      DEFAULT '{}',
    publisher      TEXT,
    published_year INTEGER,
    genres         TEXT[]      DEFAULT '{}',
    language       TEXT,
    pages          INTEGER,
    description    TEXT,
    cover_url      TEXT,
    source         TEXT,       -- 'open_library' | 'google_books'
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────────────────────────
-- DVDs / Blu-ray / other video media (populated from OMDB / TMDB)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dvds (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    barcode        TEXT        UNIQUE NOT NULL,
    title          TEXT        NOT NULL,
    director       TEXT,
    cast_members   TEXT[]      DEFAULT '{}',
    studio         TEXT,
    release_year   INTEGER,
    genres         TEXT[]      DEFAULT '{}',
    runtime_mins   INTEGER,
    rating         TEXT,       -- PG, R, 12A, etc.
    media_format   TEXT,       -- DVD | Blu-ray | 4K UHD | VHS | Other
    description    TEXT,
    cover_url      TEXT,
    source         TEXT,       -- 'omdb' | 'tmdb'
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────────────────────────
-- Scan records — one row per barcode scan attempt
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scan_records (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    barcode     TEXT        NOT NULL,
    box_number  TEXT,
    location    TEXT,
    notes       TEXT,
    media_type  TEXT,                       -- 'book' | 'dvd' | 'unknown'
    status      TEXT        NOT NULL DEFAULT 'pending',
                                            -- pending | found | not_found | error
    item_id     UUID,                       -- FK to books.id or dvds.id
    item_table  TEXT,                       -- 'books' | 'dvds'
    error_msg   TEXT,
    scanned_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scan_records_location    ON scan_records (location, box_number);
CREATE INDEX IF NOT EXISTS idx_scan_records_status      ON scan_records (status);
CREATE INDEX IF NOT EXISTS idx_scan_records_barcode     ON scan_records (barcode);
CREATE INDEX IF NOT EXISTS idx_scan_records_scanned_at  ON scan_records (scanned_at DESC);
