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

    def test_format_change_desc_detects_swaps_and_updates(self):
        a = {("men", "u1"): {"price": 10}}
        b = {("men", "u2"): {"price": 10}}
        c = {("men", "u1"): {"price": 8}}
        d = {("men", "u1"): {"price": 10}, ("men", "u2"): {"price": 12}}
        self.assertEqual(scraper.format_change_desc(a, a), "0")
        self.assertEqual(scraper.format_change_desc(a, d), "+1")
        self.assertEqual(scraper.format_change_desc(d, a), "-1")
        self.assertEqual(scraper.format_change_desc(a, b), "上新 1 / 下架 1")
        self.assertEqual(scraper.format_change_desc(a, c), "价格或信息更新")

    def test_send_feishu_notification(self):
        from unittest.mock import patch
        with patch("requests.post") as mock_post:
            mock_post.return_value = Mock(status_code=200, text='{"StatusCode":0,"StatusMessage":"success"}')
            ok = scraper.send_feishu_notification(
                total_count=100,
                total_desc="+5",
                men_count=60,
                men_desc="+3",
                women_count=40,
                women_desc="+2",
                webhook="https://example.com/fake_feishu_webhook",
                prev_total=95,
                prev_men=57,
                prev_women=38,
            )
            self.assertTrue(ok)
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            payload = call_kwargs["json"]
            self.assertEqual(payload["msg_type"], "interactive")
            card = payload["card"]
            self.assertEqual(card["schema"], "2.0")
            self.assertEqual(card["header"]["title"]["content"], "End70 产品数量变化通知")
            self.assertEqual(card["header"]["template"], "green")
            md = card["body"]["elements"][0]["content"]
            self.assertIn("**Men**：57 -> 60 <font color='green'>(+3)</font>", md)
            self.assertIn("**Women**：38 -> 40 <font color='green'>(+2)</font>", md)
            self.assertIn("**总计**：95 -> 100 <font color='green'>(+5)</font>", md)
            self.assertEqual(card["body"]["elements"][1]["tag"], "hr")
            btn = card["body"]["elements"][2]
            self.assertEqual(btn["tag"], "button")
            self.assertEqual(btn["text"]["content"], "点击查看")
            self.assertEqual(btn["behaviors"][0]["type"], "open_url")
            self.assertEqual(btn["behaviors"][0]["default_url"], "https://discount.19930216.xyz")


if __name__ == "__main__":
    unittest.main()
