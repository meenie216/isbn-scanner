# Future Improvements

## Board Game Support

### Problem
UPC Item DB has poor coverage for board games. Common titles like *Codenames* return no result, making the fast-scan path unreliable for this category.

### Root cause
Board games have many regional/edition variants with different UPCs. BoardGameGeek (BGG) — the authoritative source — stores version barcodes internally but **does not expose UPC/barcode search** in its public API (name search only).

### Proposed implementation

**Fast scan path (barcode → title):**
1. UPC Item DB (current) — try first
2. Fall back to [go-upc.com](https://go-upc.com) — better product database with board game coverage
   - Free tier: 100 requests/month
   - Needs `GO_UPC_API_KEY` in `.env` / SSM
3. If both miss: record barcode as `boardgame` type with no title; enrichment worker retries later

**Classification:**
- Add `"toys & games"`, `"board games"`, `"card games"`, `"tabletop"` to `_classify_upc()` in `backend/shared/lookup.py`
- New media type: `boardgame`

**Database:**
- New `board_games` table:
  ```sql
  CREATE TABLE board_games (
      id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      barcode        TEXT UNIQUE NOT NULL,
      title          TEXT,
      bgg_id         INTEGER,
      designer       TEXT,
      publisher      TEXT,
      year_published INTEGER,
      min_players    INTEGER,
      max_players    INTEGER,
      playing_time   INTEGER,  -- minutes
      min_age        INTEGER,
      bgg_rating     NUMERIC(4,2),
      complexity     NUMERIC(4,2),  -- BGG weight score 1-5
      categories     TEXT[],
      mechanics      TEXT[],
      cover_url      TEXT,
      enriched       BOOLEAN DEFAULT false,
      created_at     TIMESTAMPTZ DEFAULT NOW()
  );
  ```

**Background enrichment (BGG XML API v2):**
- Free, no API key required
- Rate limit: be polite (~1 req/sec)
- Step 1: `GET https://boardgamegeek.com/xmlapi2/search?query=TITLE&type=boardgame` → get BGG ID
- Step 2: `GET https://boardgamegeek.com/xmlapi2/thing?id=BGG_ID&stats=1` → full details
- Parse XML response for: designer, publisher, year, players, playing time, age, rating, weight, categories, mechanics
- Cover art: `https://cf.geekdo-images.com/...` (from `image` element in thing response)
- Add 5 board games per enrichment worker run (same pattern as DVDs/CDs)

**Frontend:**
- New `badge-boardgame` badge style
- `renderItemCard` subtitle: designer + year + `${min}–${max} players`
- Emoji: 🎲

**Files to change:**
- `backend/shared/lookup.py` — classifier + `_lookup_boardgame()`
- `backend/shared/enrich.py` — `enrich_boardgame(title, barcode)` using BGG XML API
- `backend/lookup_worker/app.py` — `_upsert_boardgame()`
- `backend/enrichment_worker/app.py` — pick 5 un-enriched board games per run
- `sql/schema.sql` — `board_games` table
- `frontend/js/app.js` — renderItemCard boardgame case
- `frontend/css/style.css` — `.badge-boardgame`
- `template.yaml` — `GO_UPC_API_KEY` SSM param (if go-upc added)
- `.env.example` — `GO_UPC_API_KEY`
- `scripts/deploy.sh` — store go-upc key in SSM
