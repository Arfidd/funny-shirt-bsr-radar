"""Pure HTML parsers for Amazon search result pages and product (/dp/) pages.

Kept free of Playwright so they can be unit-tested against saved HTML.
Amazon ships several layouts (detail bullets, product-details table, fashion
search cards), so every field has a primary selector plus text fallbacks.
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

AMAZON = "https://www.amazon.com"

# Invisible direction marks Amazon sprinkles through the detail bullets.
_MARKS = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿\xa0"), " ")

_RANK_RE = re.compile(r"#\s*([\d,]+)\s+in\s+(.+?)(?=\s+#\s*[\d,]+\s+in\s|$)")
_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%Y-%m-%d")


def clean(text):
    """Collapse whitespace and drop direction marks."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).translate(_MARKS)).strip()


def parse_price(text):
    """'$19.99' -> 19.99 ; '$1,234.50' -> 1234.5 ; '' -> None."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", clean(text))
    if not m:
        return None
    try:
        return round(float(m.group(1).replace(",", "")), 2)
    except ValueError:
        return None


def parse_date(text):
    """'October 30, 2026' -> '2026-10-30' (ISO) or None."""
    text = clean(text).strip(" :,.")
    if not text:
        return None
    m = re.search(r"([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}|\d{4}-\d{2}-\d{2})", text)
    if m:
        text = m.group(1).replace(".", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def is_captcha_page(html):
    """True when Amazon served its robot check or 503 "dogs" page instead of content."""
    low = html[:200000].lower()
    return (
        "validatecaptcha" in low
        or "enter the characters you see below" in low
        or "type the characters you see in this image" in low
        or "to discuss automated access to amazon data" in low
        or ("dogsofamazon" in low and "something went wrong" in low)
    )


# --------------------------------------------------------------------------
# Search results
# --------------------------------------------------------------------------

def _card_title_and_brand(card):
    """Fashion cards carry the brand in a plain <h2> above the linked title <h2>."""
    recipe = card.select_one('[data-cy="title-recipe"]') or card
    title = brand = ""
    for h2 in recipe.find_all("h2"):
        text = clean(h2.get("aria-label") or h2.get_text(" "))
        if not text:
            continue
        linked = h2.find_parent("a") is not None or h2.find("a") is not None
        if linked and not title:
            title = text
        elif not linked and not title and not brand:
            brand = text
    if not title and brand:
        # Single-h2 layout: that h2 is the title, brand comes from the product page.
        title, brand = brand, ""
    title = re.sub(r"^Sponsored Ad\s*[-–]\s*", "", title)
    return title, brand


def _card_sponsored(card):
    if card.select_one(".puis-sponsored-label-text, .s-sponsored-label-text, [aria-label^='Sponsored']"):
        return True
    return any(clean(s.get_text()) == "Sponsored" for s in card.find_all("span", limit=40))


def parse_search_results(html):
    """Return one dict per organic/sponsored result card, in page order."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('div[data-component-type="s-search-result"][data-asin]')
    out = []
    for pos, card in enumerate(cards, 1):
        asin = clean(card.get("data-asin"))
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            continue
        title, brand = _card_title_and_brand(card)
        img = card.select_one("img.s-image")
        price_el = card.select_one("span.a-price:not(.a-text-price) span.a-offscreen") or card.select_one(
            "span.a-price span.a-offscreen"
        )
        price_text = clean(price_el.get_text()) if price_el else ""
        out.append(
            {
                "asin": asin,
                "title": title,
                "brand": brand,
                "image": img.get("src", "") if img else "",
                "price": price_text,
                "price_value": parse_price(price_text),
                "sponsored": _card_sponsored(card),
                "search_position": pos,
                "url": f"{AMAZON}/dp/{asin}",
            }
        )
    return out


# --------------------------------------------------------------------------
# Product page
# --------------------------------------------------------------------------

def _labelled_block_text(soup, label_re):
    """Text of the <li>/<tr> that holds a detail label such as 'Best Sellers Rank'."""
    for node in soup.find_all(string=label_re):
        if node.parent is None or node.parent.name in ("script", "style", "noscript"):
            continue
        # Sub-ranks sit in a <ul> nested inside the label's own <li>, so the nearest
        # <li>/<tr> already holds every rank.
        block = node.find_parent(["li", "tr"]) or node.parent.parent
        if block is None:
            continue
        text = clean(block.get_text(" "))
        if text:
            yield text


def parse_bsr(soup):
    """Return (main_rank, main_category, [ {rank, category}, ... ]) or (None, None, [])."""
    label = re.compile(r"Best\s*Sellers\s*Rank", re.I)
    for text in _labelled_block_text(soup, label):
        text = re.sub(r"\(\s*See Top[^)]*\)", " ", text)
        text = clean(label.split(text, 1)[-1]).lstrip(": ")
        ranks = []
        for num, cat in _RANK_RE.findall(text):
            cat = clean(cat).rstrip(" .,;:")
            cat = re.split(r"\s+(?:Customer Reviews|Date First Available|ASIN|Product Dimensions)\b", cat)[0]
            ranks.append({"rank": int(num.replace(",", "")), "category": cat})
        if ranks:
            return ranks[0]["rank"], ranks[0]["category"], ranks
    return None, None, []


def parse_first_available(soup):
    label = re.compile(r"Date\s*First\s*Available", re.I)
    for text in _labelled_block_text(soup, label):
        raw = clean(label.split(text, 1)[-1]).lstrip(": ")
        iso = parse_date(raw)
        if iso:
            return iso, raw
    return None, None


def parse_byline_brand(soup):
    el = soup.select_one("#bylineInfo")
    if not el:
        return ""
    text = clean(el.get_text(" "))
    m = re.match(r"Visit the (.+?) Store$", text) or re.match(r"Brand:\s*(.+)$", text)
    return clean(m.group(1)) if m else text


def parse_product_page(html):
    soup = BeautifulSoup(html, "html.parser")
    rank, category, ranks = parse_bsr(soup)
    iso, raw = parse_first_available(soup)
    title_el = soup.select_one("#productTitle")
    img_el = soup.select_one("#landingImage, #imgTagWrapperId img")
    price_el = soup.select_one(
        "#corePrice_feature_div span.a-offscreen, #corePriceDisplay_desktop_feature_div span.a-offscreen, "
        "#apex_desktop span.a-offscreen, #priceblock_ourprice"
    )
    price_text = clean(price_el.get_text()) if price_el else ""
    return {
        "bsr": rank,
        "bsr_category": category,
        "sub_ranks": ranks[1:],
        "date_first_available": iso,
        "date_first_available_raw": raw,
        "brand": parse_byline_brand(soup),
        "title": clean(title_el.get_text(" ")) if title_el else "",
        "image": (img_el.get("data-old-hires") or img_el.get("src", "")) if img_el else "",
        "price": price_text,
        "price_value": parse_price(price_text),
    }
