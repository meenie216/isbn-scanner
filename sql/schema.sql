-- ISBN Scanner schema
-- Run against your Neon database to initialise / reset tables.
-- WARNING: DROP statements will destroy all existing data.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

DROP TABLE IF EXISTS scan_records;
DROP TABLE IF EXISTS books;
DROP TABLE IF EXISTS dvds;
DROP TABLE IF EXISTS cds;
DROP TABLE IF EXISTS other_items;

-- ──────────────────────────────────────────────────────────────────────────────
-- Books (populated from Open Library / Google Books)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE books (
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
-- DVDs / Blu-ray / video media (populated from UPC Item DB, enriched by TMDB)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE dvds (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    barcode        TEXT        UNIQUE NOT NULL,
    title          TEXT        NOT NULL,
    director       TEXT,
    cast_members   TEXT[]      DEFAULT '{}',
    studio         TEXT,
    release_year   INTEGER,
    genres         TEXT[]      DEFAULT '{}',
    runtime_mins   INTEGER,
    rating         TEXT,
    media_format   TEXT,       -- DVD | Blu-ray | 4K UHD | VHS
    description    TEXT,
    cover_url      TEXT,
    source         TEXT,
    enriched       BOOLEAN     DEFAULT false,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────────────────────────
-- CDs / music (populated from UPC Item DB, enriched by MusicBrainz)
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE cds (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    barcode        TEXT        UNIQUE NOT NULL,
    title          TEXT        NOT NULL,
    artist         TEXT,
    label          TEXT,
    release_year   INTEGER,
    genres         TEXT[]      DEFAULT '{}',
    track_listing  TEXT[]      DEFAULT '{}',
    description    TEXT,
    cover_url      TEXT,
    source         TEXT,
    enriched       BOOLEAN     DEFAULT false,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────────────────────────
-- Other items — anything not categorised as book / dvd / cd
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE other_items (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    barcode        TEXT        UNIQUE NOT NULL,
    title          TEXT        NOT NULL,
    brand          TEXT,
    category       TEXT,       -- raw UPC Item DB category string
    description    TEXT,
    cover_url      TEXT,
    source         TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ──────────────────────────────────────────────────────────────────────────────
-- Scan records — one row per barcode scan attempt
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE scan_records (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    barcode     TEXT        NOT NULL,
    box_number  TEXT,
    location    TEXT,
    notes       TEXT,
    media_type  TEXT,       -- 'book' | 'dvd' | 'cd' | 'other' | 'unknown'
    status      TEXT        NOT NULL DEFAULT 'pending',
                            -- pending | found | not_found | error
    item_id     UUID,       -- FK to books/dvds/cds/other_items
    item_table  TEXT,       -- 'books' | 'dvds' | 'cds' | 'other_items'
    error_msg   TEXT,
    scanned_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_scan_records_location   ON scan_records (location, box_number);
CREATE INDEX idx_scan_records_status     ON scan_records (status);
CREATE INDEX idx_scan_records_barcode    ON scan_records (barcode);
CREATE INDEX idx_scan_records_scanned_at ON scan_records (scanned_at DESC);

-- ──────────────────────────────────────────────────────────────────────────────
-- Migration: retry support on scan_records
-- Run this against existing databases (schema.sql DROP/CREATE handles new ones)
-- ──────────────────────────────────────────────────────────────────────────────
ALTER TABLE scan_records
    ADD COLUMN IF NOT EXISTS retry_count    INTEGER     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_retried_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_scan_records_retry
    ON scan_records (status, retry_count, last_retried_at)
    WHERE status IN ('not_found', 'error');
