"""
enrich.py — deeper metadata lookup for DVDs and CDs.

Called by the enrichment_worker Lambda (scheduled, not in the scan path).

DVDs/TV series: TMDB (free API key required)
  - Tries movie search first, then TV search
  - Returns director, cast, genres, runtime, rating, description, poster

CDs: MusicBrainz (no key, 1 req/sec rate limit, User-Agent required)
  - Tries barcode lookup first, then title+artist search
  - Returns artist, label, release_year, genres, track_listing, cover art
"""

import os
import re
import time
import requests

TMDB_BASE        = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE  = "https://image.tmdb.org/t/p/w500"
MB_BASE          = "https://musicbrainz.org/ws/2"
CAA_BASE         = "https://coverartarchive.org/release"
MB_USER_AGENT    = "isbn-scanner/1.0 (personal cataloguing app)"
REQUEST_TIMEOUT  = 10


class EnrichError(Exception):
    pass


# ── DVDs via TMDB ─────────────────────────────────────────────────────────────

def enrich_dvd(title: str, barcode: str) -> dict | None:
    """
    Search TMDB for a movie or TV series matching title.
    Returns a dict of enrichment fields, or None if nothing found.
    """
    api_key = os.environ.get("TMDB_API_KEY", "")
    if not api_key:
        return None

    params = {"api_key": api_key, "query": title, "include_adult": "false"}

    # Try movie first
    result = _tmdb_movie(api_key, params.copy())
    if result:
        return result

    # Fall back to TV
    result = _tmdb_tv(api_key, params.copy())
    return result


def _tmdb_movie(api_key: str, params: dict) -> dict | None:
    try:
        r = requests.get(f"{TMDB_BASE}/search/movie", params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        movie_id = results[0]["id"]

        # Fetch full details with credits
        r2 = requests.get(
            f"{TMDB_BASE}/movie/{movie_id}",
            params={"api_key": api_key, "append_to_response": "credits,release_dates"},
            timeout=REQUEST_TIMEOUT,
        )
        r2.raise_for_status()
        d = r2.json()

        director = next(
            (c["name"] for c in d.get("credits", {}).get("crew", []) if c["job"] == "Director"),
            None,
        )
        cast = [c["name"] for c in d.get("credits", {}).get("cast", [])[:10]]
        genres = [g["name"] for g in d.get("genres", [])]
        rating = _tmdb_movie_rating(d.get("release_dates", {}).get("results", []))
        year = int(d["release_date"][:4]) if d.get("release_date") else None
        poster = f"{TMDB_IMAGE_BASE}{d['poster_path']}" if d.get("poster_path") else None

        return {
            "director":     director,
            "cast_members": cast,
            "genres":       genres,
            "runtime_mins": d.get("runtime") or None,
            "rating":       rating,
            "release_year": year,
            "description":  d.get("overview") or None,
            "cover_url":    poster,
            "enrich_source": "tmdb_movie",
        }
    except Exception:
        return None


def _tmdb_movie_rating(release_dates: list) -> str | None:
    """Extract US or AU certification from TMDB release_dates."""
    for priority in ("AU", "US", "GB"):
        for entry in release_dates:
            if entry.get("iso_3166_1") == priority:
                for rd in entry.get("release_dates", []):
                    cert = rd.get("certification", "").strip()
                    if cert:
                        return cert
    return None


def _tmdb_tv(api_key: str, params: dict) -> dict | None:
    try:
        r = requests.get(f"{TMDB_BASE}/search/tv", params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        tv_id = results[0]["id"]

        r2 = requests.get(
            f"{TMDB_BASE}/tv/{tv_id}",
            params={"api_key": api_key, "append_to_response": "credits,content_ratings"},
            timeout=REQUEST_TIMEOUT,
        )
        r2.raise_for_status()
        d = r2.json()

        # Creator(s) as director equivalent
        creators = [c["name"] for c in d.get("created_by", [])]
        cast = [c["name"] for c in d.get("credits", {}).get("cast", [])[:10]]
        genres = [g["name"] for g in d.get("genres", [])]
        rating = _tmdb_tv_rating(d.get("content_ratings", {}).get("results", []))
        year = int(d["first_air_date"][:4]) if d.get("first_air_date") else None
        poster = f"{TMDB_IMAGE_BASE}{d['poster_path']}" if d.get("poster_path") else None
        ep_count = d.get("number_of_episodes")
        overview = d.get("overview") or None
        if ep_count:
            overview = f"{overview} ({ep_count} episodes)" if overview else f"{ep_count} episodes"

        return {
            "director":     ", ".join(creators) if creators else None,
            "cast_members": cast,
            "genres":       genres,
            "runtime_mins": (d.get("episode_run_time") or [None])[0],
            "rating":       rating,
            "release_year": year,
            "description":  overview,
            "cover_url":    poster,
            "enrich_source": "tmdb_tv",
        }
    except Exception:
        return None


def _tmdb_tv_rating(content_ratings: list) -> str | None:
    for priority in ("AU", "US", "GB"):
        for entry in content_ratings:
            if entry.get("iso_3166_1") == priority:
                rating = entry.get("rating", "").strip()
                if rating:
                    return rating
    return None


# ── CDs via MusicBrainz ───────────────────────────────────────────────────────

def enrich_cd(barcode: str, title: str, artist: str | None) -> dict | None:
    """
    Look up a CD in MusicBrainz.
    Tries barcode first, then title+artist search.
    Returns enrichment fields or None.
    Rate limit: caller must ensure >=1s between calls.
    """
    mbid = _mb_find_by_barcode(barcode)
    if not mbid:
        mbid = _mb_find_by_title(title, artist)
    if not mbid:
        return None
    return _mb_release_details(mbid)


def _mb_get(path: str, params: dict) -> dict | None:
    try:
        r = requests.get(
            f"{MB_BASE}/{path}",
            params={**params, "fmt": "json"},
            headers={"User-Agent": MB_USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _mb_find_by_barcode(barcode: str) -> str | None:
    data = _mb_get("release", {"query": f"barcode:{barcode}", "limit": 1})
    if not data:
        return None
    releases = data.get("releases", [])
    if releases and releases[0].get("score", 0) >= 80:
        return releases[0]["id"]
    return None


def _mb_find_by_title(title: str, artist: str | None) -> str | None:
    query = f'release:"{title}"'
    if artist:
        query += f' artist:"{artist}"'
    data = _mb_get("release", {"query": query, "limit": 1})
    if not data:
        return None
    releases = data.get("releases", [])
    if releases and releases[0].get("score", 0) >= 75:
        return releases[0]["id"]
    return None


def _mb_release_details(mbid: str) -> dict | None:
    time.sleep(1)  # MusicBrainz rate limit
    data = _mb_get(
        f"release/{mbid}",
        {"inc": "recordings+artist-credits+labels+genres+release-groups"},
    )
    if not data:
        return None

    # Artist
    artist = None
    ac = data.get("artist-credit", [])
    if ac and isinstance(ac[0], dict):
        artist = ac[0].get("artist", {}).get("name")

    # Label
    label = None
    li = data.get("label-info", [])
    if li and isinstance(li[0], dict):
        label = li[0].get("label", {}).get("name")

    # Release year
    year = None
    date = data.get("date") or (data.get("release-group") or {}).get("first-release-date", "")
    if date:
        m = re.search(r"\b(\d{4})\b", date)
        if m:
            year = int(m.group(1))

    # Genres (from release-group if not on release)
    genres = [g["name"] for g in data.get("genres", [])]
    if not genres:
        rg_genres = (data.get("release-group") or {}).get("genres", [])
        genres = [g["name"] for g in rg_genres]

    # Track listing from first medium
    tracks = []
    for medium in data.get("media", []):
        for t in medium.get("tracks", []):
            title_str = (t.get("title") or (t.get("recording") or {}).get("title") or "").strip()
            if title_str:
                tracks.append(title_str)
        if tracks:
            break

    # Cover art from Cover Art Archive
    cover = _mb_cover_art(mbid)

    return {
        "artist":       artist,
        "label":        label,
        "release_year": year,
        "genres":       genres,
        "track_listing": tracks or None,
        "cover_url":    cover,
        "enrich_source": "musicbrainz",
    }


def _mb_cover_art(mbid: str) -> str | None:
    try:
        r = requests.get(
            f"{CAA_BASE}/{mbid}",
            headers={"User-Agent": MB_USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        if r.status_code == 200:
            images = r.json().get("images", [])
            for img in images:
                if img.get("front"):
                    thumbs = img.get("thumbnails", {})
                    return thumbs.get("500") or thumbs.get("large") or img.get("image")
    except Exception:
        pass
    return None
