"""
lookup.py — ISBN and UPC/EAN lookup logic.

Barcode routing:
  ISBN-13 (978/979 prefix) or ISBN-10  →  book
    1. Open Library API (free, no key)
    2. Google Books API (free with key, optional)

  Other EAN-13 / UPC-A (DVDs, Blu-ray, etc.)  →  dvd
    1. UPC Item DB trial (free, 100/day, no key required)
       Returns: title, brand, images — sufficient for cataloguing.
"""

import os
import re
import requests

OPEN_LIBRARY_URL = "https://openlibrary.org/api/books"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
UPC_ITEM_DB_URL  = "https://api.upcitemdb.com/prod/trial/lookup"

REQUEST_TIMEOUT = 8  # seconds


class LookupError(Exception):
    pass


def lookup(barcode: str) -> dict:
    barcode = barcode.strip()
    if _is_book_isbn(barcode):
        return _lookup_book(barcode)
    return _lookup_dvd(barcode)


def is_book(barcode: str) -> bool:
    return _is_book_isbn(barcode.strip())


# ── Barcode type detection ────────────────────────────────────────────────────

def _is_book_isbn(barcode: str) -> bool:
    bc = re.sub(r"[^0-9X]", "", barcode.upper())
    if len(bc) == 13 and bc[:3] in ("978", "979"):
        return True
    if len(bc) == 10:
        return True
    return False


# ── Books ─────────────────────────────────────────────────────────────────────

def _lookup_book(barcode: str) -> dict:
    result = _open_library(barcode)
    if result:
        return result
    result = _google_books(barcode)
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


# ── DVDs / physical media ─────────────────────────────────────────────────────

def _lookup_dvd(barcode: str) -> dict:
    try:
        r = requests.get(
            UPC_ITEM_DB_URL,
            params={"upc": barcode},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data  = r.json()
        items = data.get("items", [])
        if not items:
            raise LookupError(f"DVD/media not found for barcode {barcode}")
        item  = items[0]
        title = item.get("title") or ""
        # Pick the first available image
        images = item.get("images") or []
        cover  = images[0] if images else None
        return {
            "media_type":   "dvd",
            "barcode":      barcode,
            "title":        title,
            "director":     None,
            "cast_members": [],
            "studio":       item.get("brand"),
            "release_year": None,
            "genres":       [],
            "runtime_mins": None,
            "rating":       None,
            "media_format": "DVD",
            "description":  item.get("description"),
            "cover_url":    cover,
            "source":       "upcitemdb",
        }
    except LookupError:
        raise
    except Exception as e:
        raise LookupError(f"DVD lookup failed for {barcode}: {e}")
