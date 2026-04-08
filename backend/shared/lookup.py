"""
lookup.py — ISBN and UPC/EAN lookup logic.

Barcode routing:
  ISBN-13 (978/979 prefix) or ISBN-10  →  book
    1. Open Library API (free, no key)
    2. Google Books API (free, optional key)

  Other EAN-13 / UPC-A  →  UPC Item DB (free, 100/day, no key)
    Category mapping:
      "Music"        →  cd
      "Movies & TV"  →  dvd
      everything else →  other
    Title keywords used as a fallback when category is absent.
"""

import os
import re
import requests

OPEN_LIBRARY_URL  = "https://openlibrary.org/api/books"
GOOGLE_BOOKS_URL  = "https://www.googleapis.com/books/v1/volumes"
UPC_ITEM_DB_URL   = "https://api.upcitemdb.com/prod/trial/lookup"
TROVE_URL         = "https://api.trove.nla.gov.au/v3/result"
DISCOGS_SEARCH_URL = "https://api.discogs.com/database/search"

DISCOGS_USER_AGENT = "ISBNScanner/1.0"

REQUEST_TIMEOUT = 8


class LookupError(Exception):
    pass


def lookup(barcode: str) -> dict:
    barcode = _normalise_barcode(barcode.strip())
    if _is_book_isbn(barcode):
        return _lookup_book(barcode)
    return _lookup_upc(barcode)


def is_book(barcode: str) -> bool:
    return _is_book_isbn(_normalise_barcode(barcode.strip()))


# ── Barcode type detection ────────────────────────────────────────────────────

def _normalise_barcode(barcode: str) -> str:
    """Normalise edge-case barcode formats before classification.

    SBN (Standard Book Number) is the 9-digit predecessor to ISBN-10.
    Prepending '0' converts it to a valid ISBN-10; the check digit is
    compatible so no recomputation is needed.
    """
    bc = re.sub(r"[^0-9X]", "", barcode.upper())
    if len(bc) == 9:
        return "0" + bc
    return barcode


def _is_book_isbn(barcode: str) -> bool:
    bc = re.sub(r"[^0-9X]", "", barcode.upper())
    if len(bc) == 13 and bc[:3] in ("978", "979"):
        return True
    if len(bc) == 10:
        return True
    return False


# ── UPC category → media type ─────────────────────────────────────────────────

# Keys are substrings to match (case-insensitive) in UPC Item DB's category field.
# Order matters — more specific entries first.
_CATEGORY_MAP = {
    "sound recordings": "cd",   # Media > Music & Sound Recordings > Music CDs
    "music cds":        "cd",
    "media > music":    "cd",
    "movies & tv":      "dvd",
    "movie":            "dvd",
    "blu-ray":          "dvd",
    "dvd":              "dvd",
    "media > tv":       "dvd",
}

# Title keyword fallback
_TITLE_DVD = ("blu-ray", "bluray", "dvd", "4k uhd", " uhd ")
_TITLE_CD  = (" cd", "audio cd", "compact disc", "soundtrack")


def _classify_upc(category: str, title: str) -> str:
    cat = (category or "").lower()
    for key, mtype in _CATEGORY_MAP.items():
        if key in cat:
            return mtype
    t = (title or "").lower()
    if any(k in t for k in _TITLE_DVD):
        return "dvd"
    if any(k in t for k in _TITLE_CD):
        return "cd"
    return "other"


def _detect_video_format(title: str) -> str:
    t = title.lower()
    if "4k" in t or "uhd" in t:
        return "4K UHD"
    if "blu-ray" in t or "bluray" in t:
        return "Blu-ray"
    if "vhs" in t:
        return "VHS"
    return "DVD"


def _split_cd_title(title: str, brand: str | None) -> tuple[str | None, str]:
    """Try to split 'Artist - Album' into (artist, album). Returns (None, title) on failure."""
    if " - " in title:
        artist, album = title.split(" - ", 1)
        return artist.strip(), album.strip()
    return brand, title


# ── Books ─────────────────────────────────────────────────────────────────────

def _lookup_book(barcode: str) -> dict:
    result = _open_library(barcode)
    if result:
        return result
    result = _google_books(barcode)
    if result:
        return result
    result = _trove(barcode)
    if result:
        return result
    raise LookupError(f"Book not found for barcode {barcode}")


def _open_library(barcode: str) -> dict | None:
    try:
        r = requests.get(
            OPEN_LIBRARY_URL,
            params={"bibkeys": f"ISBN:{barcode}", "format": "json", "jscmd": "data"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        key = f"ISBN:{barcode}"
        if key not in data:
            return None
        book = data[key]
        authors    = [a["name"] for a in book.get("authors", [])]
        publishers = [p["name"] for p in book.get("publishers", [])]
        subjects   = [s["name"] for s in book.get("subjects", [])]
        cover      = (book.get("cover") or {}).get("medium") or \
                     (book.get("cover") or {}).get("large")
        year = None
        if pd := book.get("publish_date"):
            m = re.search(r"\b(\d{4})\b", pd)
            if m:
                year = int(m.group(1))
        return {
            "media_type":     "book",
            "isbn":           barcode,
            "title":          book.get("title", ""),
            "authors":        authors,
            "publisher":      publishers[0] if publishers else None,
            "published_year": year,
            "genres":         subjects[:10],
            "language":       None,
            "pages":          book.get("number_of_pages"),
            "description":    None,
            "cover_url":      cover,
            "source":         "open_library",
        }
    except Exception:
        return None


def _google_books(barcode: str) -> dict | None:
    try:
        params = {"q": f"isbn:{barcode}"}
        api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
        if api_key and api_key != "none":
            params["key"] = api_key
        r = requests.get(GOOGLE_BOOKS_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data  = r.json()
        items = data.get("items", [])
        if not items:
            return None
        info  = items[0].get("volumeInfo", {})
        cover = (info.get("imageLinks") or {}).get("thumbnail")
        if cover:
            cover = cover.replace("http://", "https://")
        return {
            "media_type":     "book",
            "isbn":           barcode,
            "title":          info.get("title", ""),
            "authors":        info.get("authors", []),
            "publisher":      info.get("publisher"),
            "published_year": int(info["publishedDate"][:4]) if info.get("publishedDate") else None,
            "genres":         info.get("categories", []),
            "language":       info.get("language"),
            "pages":          info.get("pageCount"),
            "description":    info.get("description"),
            "cover_url":      cover,
            "source":         "google_books",
        }
    except Exception:
        return None


def _trove(barcode: str) -> dict | None:
    """Search Trove (National Library of Australia) by ISBN.

    Best coverage for niche Australian titles that Open Library and Google Books miss.
    API docs: https://trove.nla.gov.au/about/create-something/using-api
    Rate limit: generous for keyed requests (~100 req/sec).
    """
    api_key = os.environ.get("TROVE_API_KEY")
    if not api_key or api_key == "none":
        return None
    try:
        r = requests.get(
            TROVE_URL,
            params={
                "q":        f"isbn:{barcode}",
                "category": "book",
                "encoding": "json",
                "key":      api_key,
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()

        # Navigate: response → category[0] → records → work[]
        categories = data.get("category") or []
        if not categories:
            return None
        records = categories[0].get("records") or {}
        works = records.get("work") or []
        if not works:
            return None

        work = works[0]
        title = work.get("title") or ""
        # Remove trailing year in parens common in Trove: "My Book (2003)"
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()

        # contributors can be a list of strings or dicts
        raw_contributors = work.get("contributor") or []
        authors = []
        for c in raw_contributors:
            if isinstance(c, str):
                authors.append(c)
            elif isinstance(c, dict):
                authors.append(c.get("value") or c.get("name") or "")
        authors = [a for a in authors if a]

        # issued is typically "2005" or "2005-01-01"
        issued = work.get("issued") or ""
        year = None
        m = re.search(r"\b(\d{4})\b", issued)
        if m:
            year = int(m.group(1))

        if not title:
            return None

        return {
            "media_type":     "book",
            "isbn":           barcode,
            "title":          title,
            "authors":        authors,
            "publisher":      None,   # not in search result; would need /v3/work/{id}
            "published_year": year,
            "genres":         [],
            "language":       None,
            "pages":          None,
            "description":    None,
            "cover_url":      None,
            "source":         "trove",
        }
    except Exception:
        return None


# ── UPC (DVDs, CDs, other) ────────────────────────────────────────────────────

def _lookup_upc(barcode: str) -> dict:
    try:
        r = requests.get(UPC_ITEM_DB_URL, params={"upc": barcode}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data  = r.json()
        items = data.get("items", [])
        if not items:
            # Fallback: try Discogs (good coverage of Australian CDs/vinyl)
            discogs = _discogs(barcode)
            if discogs:
                return discogs
            raise LookupError(f"Not found for barcode {barcode}")

        item     = items[0]
        title    = item.get("title") or ""
        brand    = item.get("brand") or None
        category = item.get("category") or ""
        images   = item.get("images") or []
        cover    = images[0] if images else None
        desc     = item.get("description") or None

        media_type = _classify_upc(category, title)

        if media_type == "dvd":
            return {
                "media_type":   "dvd",
                "barcode":      barcode,
                "title":        title,
                "director":     None,
                "cast_members": [],
                "studio":       brand,
                "release_year": None,
                "genres":       [],
                "runtime_mins": None,
                "rating":       None,
                "media_format": _detect_video_format(title),
                "description":  desc,
                "cover_url":    cover,
                "source":       "upcitemdb",
            }

        if media_type == "cd":
            artist, album = _split_cd_title(title, brand)
            return {
                "media_type":  "cd",
                "barcode":     barcode,
                "title":       album,
                "artist":      artist,
                "label":       brand,
                "release_year": None,
                "genres":      [],
                "description": desc,
                "cover_url":   cover,
                "source":      "upcitemdb",
            }

        # other
        return {
            "media_type":  "other",
            "barcode":     barcode,
            "title":       title,
            "brand":       brand,
            "category":    category,
            "description": desc,
            "cover_url":   cover,
            "source":      "upcitemdb",
        }

    except LookupError:
        raise
    except Exception as e:
        raise LookupError(f"UPC lookup failed for {barcode}: {e}")


def _discogs(barcode: str) -> dict | None:
    """Search Discogs by barcode. No API key required for basic search."""
    try:
        r = requests.get(
            DISCOGS_SEARCH_URL,
            params={"barcode": barcode, "per_page": 5},
            headers={"User-Agent": DISCOGS_USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        hit = results[0]
        title    = hit.get("title") or ""
        labels   = hit.get("label") or []
        label    = labels[0] if labels else None
        genres   = hit.get("genre") or []
        year_str = hit.get("year")
        year     = int(year_str) if year_str and year_str.isdigit() else None
        cover    = hit.get("cover_image") or hit.get("thumb") or None
        artist, album = _split_cd_title(title, label)
        return {
            "media_type":   "cd",
            "barcode":      barcode,
            "title":        album,
            "artist":       artist,
            "label":        label,
            "release_year": year,
            "genres":       genres,
            "description":  None,
            "cover_url":    cover,
            "source":       "discogs",
        }
    except Exception:
        return None
