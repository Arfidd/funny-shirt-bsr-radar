# Funny Shirt BSR Radar

A two-part Amazon research tool:

1. **Scraper + GitHub Action**: every 4 hours, reads the newest
   "funny shirt" search sold by Amazon.com, opens each listing's `/dp/` page with
   polite random delays, and writes the shirts that have a **Best Sellers Rank**
   to `data/bsr_shirts.json`.
2. **Blogger dashboard**: a full Blogger theme, or an HTML/JavaScript gadget, that
   loads that JSON from GitHub, re-checks it on a timer, and lets you sort by lowest BSR,
   newest upload, top movers or price.

```
scraper/bsr_scraper.py      Playwright runner (search -> product pages -> JSON)
scraper/parsers.py          HTML parsers (no browser needed, unit-tested)
.github/workflows/update_bsr.yml
data/bsr_shirts.json        shirts WITH a BSR  (what the dashboard reads)
data/watchlist.json         new listings without a BSR yet, re-checked later
blogger/src/                dashboard.html / .css / .js (single source)
blogger/build.py            builds blogger/dist/*
blogger/dist/bsr-dashboard-theme.xml   full theme
blogger/dist/bsr-gadget.html           gadget snippet
tests/                      python -m unittest discover tests
```

## Why there is a watchlist

The search URL sorts by **newest first**, and in practice every result on the first
few pages was uploaded **the same day**. A listing only gets a BSR after its first
sale, so a single pass usually finds no ranked shirts. Instead, each run:

1. reads `--pages` search pages (default 3, about 144 listings) and adds new ASINs to the tracked set;
2. spends up to `--max-products` product-page visits (default 150): never-checked
   listings first, then whichever tracked listing was checked longest ago;
3. moves a listing into `bsr_shirts.json` as soon as it shows a BSR, and appends
   to its `bsr_history` on every later check (this powers the sparkline and "Top movers");
4. drops no-BSR listings after `--watch-days` (3) and ranked shirts after `--keep-days` (14).

If Amazon serves a robot check or its 503 "dogs" page, the run stops, keeps every
record's last known values, and adds a warning to the Action log. The JSON is only
rewritten when at least one product page was actually checked.

## 1. Set up the GitHub repo

1. Create a **public** GitHub repository (raw URLs of private repos need a token, so the
   dashboard can't read them) and push this folder as its root.
2. Go to **Settings → Actions → General → Workflow permissions** and choose
   **Read and write permissions**, so the Action can commit the JSON.
3. Go to **Actions → Update BSR shirts → Run workflow** to do the first run now.
   After that the cron (`0 */4 * * *`) runs it every 4 hours.
   - GitHub may start scheduled runs a few minutes late, and turns schedules off after
     60 days without repository activity. The bot's data commits count as activity.
   - If a run saved pages it couldn't parse, they're attached to that run as the
     `debug-pages-*` artifact.

Workflow inputs: `pages` and `max_products`. Environment overrides:
`BSR_PAGES`, `BSR_MAX_PRODUCTS`, `BSR_WATCH_DAYS`, `BSR_KEEP_DAYS`, `BSR_SEARCH_URL`.

### Run locally

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python scraper/bsr_scraper.py --max-products 10 --headful
.venv/Scripts/python -m unittest discover tests
```

## 2. Install the dashboard on Blogger

First point the dashboard at your repo, then rebuild:

```bash
python blogger/build.py --data-url https://raw.githubusercontent.com/<user>/<repo>/main/data/bsr_shirts.json
```

Or edit `blogger/config.json` and run `python blogger/build.py`. You can also edit the
`window.BSR_CONFIG` block (marked `EDIT ME`) directly in the dist file.

- **Full theme:** Blogger → **Theme** → ▾ → **Edit HTML** → replace everything with
  `blogger/dist/bsr-dashboard-theme.xml` → **Save**. The homepage becomes the dashboard.
  Individual post pages still show the post.
- **Gadget in your current theme:** **Layout** → **Add a Gadget** → **HTML/JavaScript**
  → paste `blogger/dist/bsr-gadget.html`. All of its CSS is scoped to `#bsr-app`.

Config keys: `dataUrl`, `pollMinutes` (how often the page re-checks the JSON, default 10),
`scrapeHours` (used for the "next scrape due" tooltip), `newDays` (how many days a listing
keeps its NEW tag).

Dashboard behaviour:
- It polls every `pollMinutes` while the tab is visible, checks again when you come
  back to the tab, and shows a toast when a new scrape arrives.
- After failed fetches it backs off, up to 4× the interval, and keeps showing the last good data.
- Your sort and filter choices are remembered per browser.

### Local preview

```bash
python blogger/build.py
python -m http.server 8765
```

Open http://localhost:8765/blogger/dist/preview.html. It reads `data/bsr_shirts.json`;
pass `--preview-data` to point it at another file.

## Data format (`data/bsr_shirts.json`)

```json
{
  "last_updated": "2026-11-03T18:26:52Z",
  "source_url": "https://www.amazon.com/s?k=funny+shirt&rh=p_6%3AATVPDKIKX0DER&s=date-desc-rank&dc",
  "count": 14,
  "stats": { "search_results": 144, "new_listings": 52, "checked": 150, "with_bsr": 9, "no_bsr": 141, "blocked": false, "watching": 610 },
  "shirts": [
    {
      "asin": "B0XXXXXXXX", "title": "...", "brand": "...", "image": "https://m.media-amazon.com/...",
      "price": "$19.99", "price_value": 19.99, "url": "https://www.amazon.com/dp/B0XXXXXXXX",
      "bsr": 23456, "bsr_category": "Clothing, Shoes & Jewelry",
      "sub_ranks": [{ "rank": 12, "category": "Men's Novelty T-Shirts" }],
      "date_first_available": "2026-10-30", "date_first_available_raw": "October 30, 2026",
      "first_seen": "...", "bsr_first_seen": "...", "last_checked": "...", "checks": 3,
      "search_position": 7, "bsr_history": [{ "t": "...", "bsr": 30111 }, { "t": "...", "bsr": 23456 }]
    }
  ]
}
```

`price` is often empty for brand-new listings, because Amazon shows "See options" until a
size is picked.

## Notes and limits

- Amazon's Conditions of Use restrict automated access, and its HTML changes often.
  This tool is low-volume on purpose: one browser session per run, 4–9 s random gaps
  between pages, images/fonts/media not downloaded, and a stop on the first robot check.
  It does not solve CAPTCHAs or rotate proxies.
- GitHub-hosted runners use data-centre IPs, which Amazon challenges more often than home
  connections. If most scheduled runs end with "blocked" warnings, the options are
  Amazon's official Product Advertising API (needs an Associates account) or a paid
  product-data API. Either can replace `AmazonSession.get()` without touching the parsers' output format.
