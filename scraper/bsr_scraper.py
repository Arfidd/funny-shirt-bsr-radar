"""Amazon BSR research scraper.

Reads the newest "funny shirt" search (sold by Amazon.com), opens result /dp/
pages with polite randomised delays and keeps only shirts that show a Best
Sellers Rank in data/bsr_shirts.json.

The newest-first search is almost entirely same-day uploads, and a listing
only gets a BSR after its first sale. So listings without a BSR go on a
watchlist (data/watchlist.json) and are re-checked on later runs for
--watch-days; shirts that have a BSR are refreshed (building a rank history)
for --keep-days. Each run spends at most --max-products page visits, new
listings first, then whatever was checked longest ago.

    python scraper/bsr_scraper.py                  # defaults used by the GitHub Action
    python scraper/bsr_scraper.py --max-products 5 --headful

If Amazon answers with a robot check, the run stops early and everything not
re-checked keeps its last known values, so the dashboard never goes blank.
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parsers import AMAZON, is_captcha_page, parse_product_page, parse_search_results  # noqa: E402

SEARCH_URL = "https://www.amazon.com/s?k=funny+shirt&rh=p_6%3AATVPDKIKX0DER&s=date-desc-rank&dc"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "bsr_shirts.json"
DEFAULT_WATCH = ROOT / "data" / "watchlist.json"
HISTORY_POINTS = 42  # 7 days of 4-hourly runs

CARD_KEYS = ("title", "brand", "image", "price", "price_value", "url")
DETAIL_KEYS = ("bsr", "bsr_category", "sub_ranks", "date_first_available", "date_first_available_raw")

# Hides the most obvious automation tells; headers are set on the context.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""


def log(msg, level="info"):
    """Plain log line; warnings also become GitHub Actions annotations."""
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if level == "warning" and os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::{msg}", flush=True)
    else:
        print(f"[{stamp}] {msg}", flush=True)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def polite_sleep(lo, hi):
    time.sleep(random.uniform(lo, hi))


def page_url(base, n):
    return base if n == 1 else f"{base}&page={n}"


class Blocked(Exception):
    """Amazon served a robot check."""


class AmazonSession:
    """One Chromium context with realistic headers, reused for every request."""

    def __init__(self, pw, headful):
        try:
            # channel="chromium" = new headless mode, which looks like regular Chrome.
            self.browser = pw.chromium.launch(
                headless=not headful, channel="chromium", args=["--disable-blink-features=AutomationControlled"]
            )
        except PlaywrightError:
            self.browser = pw.chromium.launch(headless=not headful, args=["--disable-blink-features=AutomationControlled"])
        major = self.browser.version.split(".")[0]
        ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        self.context = self.browser.new_context(
            user_agent=ua,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        self.context.add_init_script(STEALTH_JS)
        self.context.add_cookies(
            [
                {"name": "i18n-prefs", "value": "USD", "domain": ".amazon.com", "path": "/"},
                {"name": "lc-main", "value": "en_US", "domain": ".amazon.com", "path": "/"},
            ]
        )
        self.page = self.context.new_page()
        # Only the HTML is needed: skipping images/fonts/media keeps the footprint small.
        self.page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )

    def warm_up(self):
        """Open the homepage first so the search request carries normal session cookies."""
        try:
            self.page.goto(AMAZON + "/", wait_until="domcontentloaded", timeout=45000)
            self.page.wait_for_timeout(random.randint(1500, 3000))
        except (PlaywrightTimeout, PlaywrightError) as exc:
            log(f"homepage warm-up failed ({exc.__class__.__name__}); continuing")

    def get(self, url, wait_selector, referer=None):
        """Load url and return its HTML; raises Blocked on a robot check."""
        self.page.goto(url, wait_until="domcontentloaded", timeout=45000, referer=referer)
        try:
            self.page.wait_for_selector(wait_selector, timeout=15000)
        except PlaywrightTimeout:
            pass  # parse whatever arrived; the captcha check below decides
        html = self.page.content()
        if is_captcha_page(html):
            raise Blocked(url)
        return html

    def close(self):
        self.context.close()
        self.browser.close()


def fetch_with_retry(session, url, wait_selector, backoff, referer=None):
    """One retry after a long pause when blocked, then give up."""
    for attempt in (1, 2):
        try:
            return session.get(url, wait_selector, referer)
        except Blocked:
            if attempt == 2:
                raise
            log(f"robot check on {url}; backing off {backoff:.0f}s", "warning")
            time.sleep(backoff)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            if attempt == 2:
                raise
            log(f"load error on {url}: {exc.__class__.__name__}; retrying")
            polite_sleep(5, 10)
    return None


def dump_debug(folder, name, html):
    """Save an unexpected page so the layout can be inspected (CI uploads this folder)."""
    if not folder:
        return
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.html"
    path.write_text(html, encoding="utf-8")
    log(f"  saved page to {path}")


# --------------------------------------------------------------------------
# Tracked listings (bsr_shirts.json + watchlist.json)
# --------------------------------------------------------------------------

def load_records(path, key):
    try:
        with open(path, encoding="utf-8") as fh:
            return {r["asin"]: r for r in json.load(fh).get(key, []) if r.get("asin")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def absorb_card(rec, card):
    """Fill gaps from the latest search card and note where it sits in the search."""
    for key in CARD_KEYS:
        if card.get(key) and not rec.get(key):
            rec[key] = card[key]
    rec["search_position"] = card["search_position"]
    rec["sponsored"] = card["sponsored"]


def apply_detail(rec, detail, checked_at):
    """Store a product-page check on the record and extend its BSR history."""
    for key in CARD_KEYS:
        if detail.get(key):
            # The product page is authoritative for brand/title; card wins for the
            # small search thumbnail.
            if key == "image" and rec.get("image"):
                continue
            rec[key] = detail[key]
    for key in DETAIL_KEYS:
        rec[key] = detail.get(key)
    rec["last_checked"] = checked_at
    rec["checks"] = rec.get("checks", 0) + 1
    if rec["bsr"] is not None:
        rec["bsr_checked_at"] = checked_at
        rec.setdefault("bsr_first_seen", checked_at)
        history = list(rec.get("bsr_history") or [])
        history.append({"t": checked_at, "bsr": rec["bsr"]})
        rec["bsr_history"] = history[-HISTORY_POINTS:]


def build_queue(tracked, limit):
    """Never-checked listings first (search order), then the longest-unchecked ones."""
    fresh = sorted((r for r in tracked.values() if not r.get("last_checked")), key=lambda r: r.get("search_position") or 9999)
    seen = sorted((r for r in tracked.values() if r.get("last_checked")), key=lambda r: r["last_checked"])
    queue = fresh + seen
    return queue[:limit] if limit else queue


def prune(tracked, now, keep_days, watch_days):
    """Drop BSR shirts after keep_days and no-BSR listings after watch_days (from first_seen)."""
    kept = {}
    for asin, rec in tracked.items():
        first = parse_ts(rec.get("first_seen")) or now
        limit = keep_days if rec.get("bsr") is not None else watch_days
        if now - first <= timedelta(days=limit):
            kept[asin] = rec
    return kept


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def run(args):
    started = utc_now()
    tracked = load_records(args.watchlist, "items")
    tracked.update(load_records(args.out, "shirts"))
    stats = {"search_results": 0, "new_listings": 0, "checked": 0, "with_bsr": 0, "no_bsr": 0, "blocked": False}

    with sync_playwright() as pw:
        session = AmazonSession(pw, args.headful)
        try:
            session.warm_up()

            # 1) Search pages -> merge cards into the tracked set
            for rec in tracked.values():
                rec["search_position"] = None
            seen = set()
            for n in range(1, args.pages + 1):
                url = page_url(args.url, n)
                log(f"search page {n}: {url}")
                try:
                    html = fetch_with_retry(
                        session, url, 'div[data-component-type="s-search-result"]', args.backoff, AMAZON + "/"
                    )
                except Blocked:
                    stats["blocked"] = True
                    dump_debug(args.debug_dir, f"search_p{n}_blocked", session.page.content())
                    log("search page blocked by robot check; keeping previous data", "warning")
                    break
                found = parse_search_results(html)
                log(f"  {len(found)} cards")
                if not found:
                    dump_debug(args.debug_dir, f"search_p{n}", html)
                    break
                for card in found:
                    if card["asin"] in seen or (card["sponsored"] and not args.include_sponsored):
                        continue
                    seen.add(card["asin"])
                    card["search_position"] = len(seen)
                    if card["asin"] not in tracked:
                        tracked[card["asin"]] = {"asin": card["asin"], "first_seen": started}
                        stats["new_listings"] += 1
                    absorb_card(tracked[card["asin"]], card)
                if n < args.pages:
                    polite_sleep(args.min_delay, args.max_delay)
            stats["search_results"] = len(seen)

            # 2) Product pages, within the per-run budget
            queue = [] if stats["blocked"] else build_queue(tracked, args.max_products)
            log(f"checking {len(queue)} product pages ({stats['new_listings']} new, {len(tracked)} tracked)")
            for i, rec in enumerate(queue, 1):
                polite_sleep(args.min_delay, args.max_delay)
                url = f"{AMAZON}/dp/{rec['asin']}"
                try:
                    html = fetch_with_retry(session, url, "#productTitle", args.backoff, args.url)
                except Blocked:
                    stats["blocked"] = True
                    log(f"blocked at product {i}/{len(queue)}; stopping product checks", "warning")
                    break
                except (PlaywrightTimeout, PlaywrightError) as exc:
                    log(f"  {rec['asin']}: failed to load ({exc.__class__.__name__}); will retry next run")
                    continue
                if "productTitle" not in html:
                    log(f"  {rec['asin']}: not a product page (listing removed?)")
                    rec["last_checked"] = utc_now()
                    continue
                apply_detail(rec, parse_product_page(html), utc_now())
                stats["checked"] += 1
                if rec["bsr"] is None:
                    stats["no_bsr"] += 1
                    log(f"  [{i}/{len(queue)}] {rec['asin']}: no BSR yet")
                else:
                    stats["with_bsr"] += 1
                    log(f"  [{i}/{len(queue)}] {rec['asin']}: BSR #{rec['bsr']:,} | first available {rec['date_first_available']}")
        finally:
            session.close()

    if not stats["checked"]:
        log("no product pages checked this run; leaving existing JSON untouched", "warning")
        return 0

    now = datetime.now(timezone.utc)
    tracked = prune(tracked, now, args.keep_days, args.watch_days)
    shirts = sorted((r for r in tracked.values() if r.get("bsr") is not None), key=lambda r: r["bsr"])
    watching = sorted((r for r in tracked.values() if r.get("bsr") is None), key=lambda r: r["first_seen"])
    stats["watching"] = len(watching)
    stamp = utc_now()

    write_json(args.watchlist, {"last_updated": stamp, "watch_days": args.watch_days, "count": len(watching), "items": watching})
    write_json(
        args.out,
        {
            "last_updated": stamp,
            "run_started": started,
            "source_url": args.url,
            "count": len(shirts),
            "stats": stats,
            "shirts": shirts,
        },
    )
    log(f"wrote {len(shirts)} shirts with BSR, watching {len(watching)} more ({stats})")
    return 0


def parse_args(argv=None):
    env = os.environ.get
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=env("BSR_SEARCH_URL", SEARCH_URL))
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--watchlist", type=Path, default=DEFAULT_WATCH)
    p.add_argument("--pages", type=int, default=int(env("BSR_PAGES", "3")), help="search result pages to read")
    p.add_argument("--max-products", type=int, default=int(env("BSR_MAX_PRODUCTS", "150")), help="product page visits per run (0 = no cap)")
    p.add_argument("--watch-days", type=float, default=float(env("BSR_WATCH_DAYS", "3")), help="re-check no-BSR listings this long")
    p.add_argument("--keep-days", type=float, default=float(env("BSR_KEEP_DAYS", "14")), help="keep BSR shirts this long")
    p.add_argument("--min-delay", type=float, default=4.0, help="seconds between requests (min)")
    p.add_argument("--max-delay", type=float, default=9.0, help="seconds between requests (max)")
    p.add_argument("--backoff", type=float, default=60.0, help="pause before retrying after a robot check")
    p.add_argument("--include-sponsored", action="store_true", help="keep sponsored result cards")
    p.add_argument("--headful", action="store_true", help="show the browser window (local debugging)")
    p.add_argument("--debug-dir", type=Path, default=ROOT / "debug", help="where unexpected pages are saved")
    args = p.parse_args(argv)
    args.max_delay = max(args.max_delay, args.min_delay)
    return args


if __name__ == "__main__":
    sys.exit(run(parse_args()))
