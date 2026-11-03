"""Parser tests against hand-built snippets of the Amazon layouts we support.

    python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
from parsers import is_captcha_page, parse_date, parse_price, parse_product_page, parse_search_results  # noqa: E402

SEARCH_HTML = """
<div class="s-main-slot">
  <div data-component-type="s-search-result" data-asin="B0FASHION1">
    <div data-cy="title-recipe">
      <div class="a-row a-color-secondary"><h2 class="a-size-mini"><span class="a-size-base-plus a-color-base">Goofy Tees Co</span></h2></div>
      <a class="a-link-normal" href="/Goofy/dp/B0FASHION1"><h2 aria-label="I Paused My Game To Be Here Funny Gamer T-Shirt" class="a-size-base-plus"><span>I Paused My Game To Be Here Funny Gamer T-Shirt</span></h2></a>
    </div>
    <img class="s-image" src="https://m.media-amazon.com/images/I/81abc._AC_UL320_.jpg"/>
    <span class="a-price"><span class="a-offscreen">$19.99</span><span aria-hidden="true">$19<span>99</span></span></span>
    <span class="a-price a-text-price"><span class="a-offscreen">$24.99</span></span>
  </div>
  <div data-component-type="s-search-result" data-asin="B0SPONSOR2">
    <span class="puis-sponsored-label-text">Sponsored</span>
    <h2><a href="/x"><span>Sponsored Ad - Taco Tuesday Shirt</span></a></h2>
    <span class="a-price"><span class="a-offscreen">$1,018.50</span></span>
  </div>
  <div data-component-type="s-search-result" data-asin="">
    <h2><a href="/x"><span>Widget placeholder</span></a></h2>
  </div>
</div>
"""

BULLETS_HTML = """
<span id="productTitle"> Funny Cat Dad Shirt </span>
<div id="bylineInfo">Visit the Meow Merch Store</div>
<div id="detailBullets_feature_div"><ul class="a-unordered-list detail-bullet-list">
  <li><span class="a-list-item"><span class="a-text-bold">Date First Available &rlm; : &lrm;</span><span>October 30, 2026</span></span></li>
  <li><span class="a-list-item"><span class="a-text-bold">ASIN &rlm; : &lrm;</span><span>B0BULLET01</span></span></li>
</ul></div>
<ul class="a-unordered-list a-nostyle a-vertical a-spacing-none detail-bullet-list">
  <li><span class="a-list-item"><span class="a-text-bold">Best Sellers Rank:</span> #23,456 in Clothing, Shoes &amp; Jewelry (<a href="/gp/bestsellers/fashion">See Top 100 in Clothing, Shoes &amp; Jewelry</a>)
    <ul class="a-unordered-list a-nostyle a-vertical zg_hrsr">
      <li><span class="a-list-item"> #12 in <a href="/x">Men's Novelty T-Shirts</a></span></li>
      <li><span class="a-list-item"> #1,402 in <a href="/y">Women's Novelty T-Shirts</a></span></li>
    </ul></span></li>
  <li><span class="a-list-item"><span class="a-text-bold">Customer Reviews:</span> 4.7 out of 5 stars</span></li>
</ul>
"""

TABLE_HTML = """
<div id="bylineInfo">Brand: Pun Factory</div>
<table id="productDetails_detailBullets_sections1">
  <tr><th class="prodDetSectionEntry">ASIN</th><td>B0TABLE001</td></tr>
  <tr><th class="prodDetSectionEntry">Best Sellers Rank</th><td><span><span>#1,234,567 in Clothing, Shoes &amp; Jewelry (<a>See Top 100 in Clothing, Shoes &amp; Jewelry</a>)</span><br/><span>#9,876 in <a>Men's Novelty T-Shirts</a></span></span></td></tr>
  <tr><th class="prodDetSectionEntry">Date First Available</th><td>Nov 2, 2026</td></tr>
</table>
<script>var x = "Best Sellers Rank #1 in Everything";</script>
"""

NO_BSR_HTML = """
<span id="productTitle">Brand New Shirt</span>
<ul><li><span class="a-text-bold">Date First Available :</span> November 3, 2026</li></ul>
<script>var meta = {"label":"Best Sellers Rank"};</script>
"""

CAPTCHA_HTML = """<html><body><form method="get" action="/errors/validateCaptcha">
<h4>Enter the characters you see below</h4></form></body></html>"""


class SearchResults(unittest.TestCase):
    def test_cards(self):
        cards = parse_search_results(SEARCH_HTML)
        self.assertEqual([c["asin"] for c in cards], ["B0FASHION1", "B0SPONSOR2"])
        a, b = cards
        self.assertEqual(a["brand"], "Goofy Tees Co")
        self.assertEqual(a["title"], "I Paused My Game To Be Here Funny Gamer T-Shirt")
        self.assertEqual(a["price"], "$19.99")
        self.assertEqual(a["price_value"], 19.99)
        self.assertTrue(a["image"].startswith("https://m.media-amazon.com/"))
        self.assertFalse(a["sponsored"])
        self.assertEqual(a["url"], "https://www.amazon.com/dp/B0FASHION1")
        self.assertTrue(b["sponsored"])
        self.assertEqual(b["title"], "Taco Tuesday Shirt")
        self.assertEqual(b["brand"], "")
        self.assertEqual(b["price_value"], 1018.5)


class ProductPage(unittest.TestCase):
    def test_detail_bullets(self):
        d = parse_product_page(BULLETS_HTML)
        self.assertEqual(d["bsr"], 23456)
        self.assertEqual(d["bsr_category"], "Clothing, Shoes & Jewelry")
        self.assertEqual(d["sub_ranks"], [
            {"rank": 12, "category": "Men's Novelty T-Shirts"},
            {"rank": 1402, "category": "Women's Novelty T-Shirts"},
        ])
        self.assertEqual(d["date_first_available"], "2026-10-30")
        self.assertEqual(d["brand"], "Meow Merch")
        self.assertEqual(d["title"], "Funny Cat Dad Shirt")

    def test_details_table(self):
        d = parse_product_page(TABLE_HTML)
        self.assertEqual(d["bsr"], 1234567)
        self.assertEqual(d["sub_ranks"], [{"rank": 9876, "category": "Men's Novelty T-Shirts"}])
        self.assertEqual(d["date_first_available"], "2026-11-02")
        self.assertEqual(d["brand"], "Pun Factory")

    def test_no_bsr_ignores_scripts(self):
        d = parse_product_page(NO_BSR_HTML)
        self.assertIsNone(d["bsr"])
        self.assertEqual(d["date_first_available"], "2026-11-03")

    def test_captcha(self):
        self.assertTrue(is_captcha_page(CAPTCHA_HTML))
        self.assertFalse(is_captcha_page(BULLETS_HTML))


class Helpers(unittest.TestCase):
    def test_price(self):
        self.assertEqual(parse_price("$1,234.50"), 1234.5)
        self.assertIsNone(parse_price(""))

    def test_date(self):
        self.assertEqual(parse_date("‏ : ‎ September 5, 2026"), "2026-09-05")
        self.assertEqual(parse_date("5 November 2026"), "2026-11-05")
        self.assertIsNone(parse_date("soon"))
        self.assertEqual(parse_date("Aug. 9, 2026"), "2026-08-09")


if __name__ == "__main__":
    unittest.main()
