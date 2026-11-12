import unittest
from unittest.mock import Mock

import scrape_fast as scraper


class FastScraperTests(unittest.TestCase):
    def sample_hit(self, orig=19, disc=7, handle="beams-japan-toothbrush-5674-0229-546-01"):
        return {
            "handle": handle,
            "title": "BEAMS JAPAN Toothbrush",
            "meta": {"colour": {"name": "White"}},
            "product_image": "https://cdn.shopify.com/s/files/1/1011/8339/0034/files/toothbrush.jpg?v=1788611048",
            "market_pricing_usd": {
                "compare_at_price": orig,
                "price": disc,
                "price_ratio": disc / orig if orig else 1,
            },
        }

    def test_extracts_product_and_rounds_discount_to_nearest_5_percent(self):
        # $19 -> $7 为 63.16% off，应按网站规则归入 65% off
        product = scraper.extract_product_from_hit(self.sample_hit(19, 7), "women")
        self.assertEqual(product["type"], "women")
        self.assertEqual(product["name"], "BEAMS JAPAN Toothbrush - White")
        self.assertEqual(product["original_price"], 19)
        self.assertEqual(product["discounted_price"], 7)
        self.assertEqual(product["currency"], "USD")
        self.assertEqual(product["discount"], "65% off")
        self.assertEqual(
            product["url"],
            "https://www.endclothing.com/en-cn/products/beams-japan-toothbrush-5674-0229-546-01",
        )
        self.assertEqual(
            product["image_url"],
            "https://www.endclothing.com/cdn/shop/files/toothbrush.jpg?v=1788611048&width=2100",
        )

    def test_filters_out_non_target_discounts(self):
        # 50% off 和 80% off 均不在 60/65/70% off 目标范围内
        self.assertIsNone(scraper.extract_product_from_hit(self.sample_hit(100, 50), "men"))
        self.assertIsNone(scraper.extract_product_from_hit(self.sample_hit(100, 20), "men"))

    def test_scrape_all_combines_men_and_women_in_single_batch(self):
        session = Mock()
        session.post.return_value = Mock(
            status_code=200,
            json=lambda: {
                "results": [
                    {"nbHits": 1, "nbPages": 1, "hits": [self.sample_hit(79, 32, "men-tee")]},
                    {"nbHits": 1, "nbPages": 1, "hits": [self.sample_hit(19, 7, "women-item")]},
                ]
            },
        )
        results = scraper.scrape_all(session)
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(len(results["men"]), 1)
        self.assertEqual(len(results["women"]), 1)


if __name__ == "__main__":
    unittest.main()
