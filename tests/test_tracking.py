"""Watchlist bookkeeping: queue order, history and retention.

    python -m unittest discover tests
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
from bsr_scraper import HISTORY_POINTS, apply_detail, build_queue, prune  # noqa: E402

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def detail(bsr=None, **kw):
    base = {"bsr": bsr, "bsr_category": "Clothing" if bsr else None, "sub_ranks": [], "date_first_available": "2026-11-01",
            "date_first_available_raw": "November 1, 2026", "brand": "Pun Co", "title": "Shirt", "image": "https://x/big.jpg",
            "price": "", "price_value": None}
    base.update(kw)
    return base


class Queue(unittest.TestCase):
    def test_new_first_then_oldest_checked(self):
        tracked = {
            "A": {"asin": "A", "last_checked": "2026-11-03T08:00:00Z"},
            "B": {"asin": "B", "search_position": 5},
            "C": {"asin": "C", "last_checked": "2026-11-02T08:00:00Z"},
            "D": {"asin": "D", "search_position": 2},
        }
        self.assertEqual([r["asin"] for r in build_queue(tracked, 0)], ["D", "B", "C", "A"])
        self.assertEqual([r["asin"] for r in build_queue(tracked, 3)], ["D", "B", "C"])


class Detail(unittest.TestCase):
    def test_history_and_card_image_kept(self):
        rec = {"asin": "A", "image": "https://x/thumb.jpg"}
        apply_detail(rec, detail(None), "2026-11-03T00:00:00Z")
        self.assertIsNone(rec["bsr"])
        self.assertNotIn("bsr_history", rec)
        for i in range(HISTORY_POINTS + 5):
            apply_detail(rec, detail(1000 + i), f"2026-11-03T{i % 24:02d}:00:00Z")
        self.assertEqual(rec["image"], "https://x/thumb.jpg")
        self.assertEqual(rec["brand"], "Pun Co")
        self.assertEqual(len(rec["bsr_history"]), HISTORY_POINTS)
        self.assertEqual(rec["bsr_history"][-1]["bsr"], 1000 + HISTORY_POINTS + 4)
        self.assertEqual(rec["bsr_first_seen"], "2026-11-03T00:00:00Z")
        self.assertEqual(rec["checks"], HISTORY_POINTS + 6)


class Prune(unittest.TestCase):
    def test_windows(self):
        tracked = {
            "old_watch": {"asin": "old_watch", "first_seen": "2026-10-30T00:00:00Z", "bsr": None},
            "new_watch": {"asin": "new_watch", "first_seen": "2026-11-02T00:00:00Z", "bsr": None},
            "old_bsr": {"asin": "old_bsr", "first_seen": "2026-10-10T00:00:00Z", "bsr": 5000},
            "kept_bsr": {"asin": "kept_bsr", "first_seen": "2026-10-25T00:00:00Z", "bsr": 5000},
        }
        self.assertEqual(sorted(prune(tracked, NOW, keep_days=14, watch_days=3)), ["kept_bsr", "new_watch"])


if __name__ == "__main__":
    unittest.main()
