import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from unittest import mock

import web_log


class WebLogParserTests(unittest.TestCase):
    def test_convert_utc_iso_to_shanghai(self):
        src = 'info: done {"timestamp":"2026-11-13T01:00:03.151Z"}'
        converted = web_log.convert_utc_iso_to_shanghai(src)
        self.assertIn("2026-11-13 09:00:03.151", converted)

    def test_parse_end70_runs_normal_push_and_error(self):
        sample = """==================== [2026-11-13 08:10:01] Scrape Started ====================
Loaded 848 existing products.
Scrape failed: UnboundLocalError: cannot access local variable 'final_count'
==================== [2026-11-13 08:10:02] Finished (Exit: 1) ====================
==================== [2026-11-13 09:15:02] Scrape Started ====================
Loaded 848 existing products.
[men] Algolia pre-filtered hits=479 pages=1
[men] matched target discounts (60%/65%/70% off): 478
[women] Algolia pre-filtered hits=371 pages=1
[women] matched target discounts (60%/65%/70% off): 370
No product changes detected; skipping file write and notification.
Complete: total=848 (men=478, women=370) changed=False elapsed=1.44s
==================== [2026-11-13 09:15:03] Finished (Exit: 0) ====================
==================== [2026-11-13 09:20:01] Scrape Started ====================
Loaded 848 existing products.
[men] Algolia pre-filtered hits=479 pages=1
[men] matched target discounts (60%/65%/70% off): 478
[women] Algolia pre-filtered hits=370 pages=1
[women] matched target discounts (60%/65%/70% off): 369
Feishu notification sent: status=200, response={"StatusCode":0,"StatusMessage":"success"}
Complete: total=847 (men=478, women=369) changed=True elapsed=2.25s
==================== [2026-11-13 09:20:04] Finished (Exit: 0) ===================="""

        runs, avg = web_log.parse_log_runs("end70", sample)
        self.assertEqual(len(runs), 3)
        # 倒序：最新在第一条
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "848 → 847 (-1 items)")
        self.assertEqual(runs[0]["fields"][1][1], "Men: 478 -> 478 (0)\nWomen: 370 -> 369 (-1)")
        self.assertIn("Men：478 -> 478 (0)", runs[0]["push_preview"])
        self.assertIn("Women：370 -> 369 (-1)", runs[0]["push_preview"])
        self.assertIn("总计：848 -> 847 (-1)", runs[0]["push_preview"])
        self.assertIn("链接：https://discount.19930216.xyz", runs[0]["push_preview"])
        self.assertEqual(runs[0]["fields"][3], ["Link", "https://discount.19930216.xyz"])
        self.assertEqual(runs[1]["status"], "ok")
        self.assertEqual(runs[1]["summary"], "848 items (478M / 370W)")
        self.assertIn("Total: 848 -> 848 (0)", runs[1]["fields"][2][1])
        self.assertEqual(runs[1]["fields"][3], ["Link", "https://discount.19930216.xyz"])
        self.assertEqual(runs[2]["status"], "err")
        self.assertIn("UnboundLocalError", runs[2]["summary"])
        self.assertNotEqual(avg, "--")

    def test_parse_end70_compact_log_format(self):
        sample = """==================== [2026-11-13 09:50:01] Scrape Started ====================
Men: 479 -> 478 (-1)
Women: 369 -> 371 (+2)
Total: 848 -> 849 (+1)
Link: https://discount.19930216.xyz
Feishu notification sent: status=200, response={"StatusCode":0,"StatusMessage":"success"}
Complete: total=849 (men=478, women=371) changed=True elapsed=1.95s
==================== [2026-11-13 09:50:03] Finished (Exit: 0) ===================="""

        runs, _ = web_log.parse_log_runs("end70", sample)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "848 → 849 (+1 items)")
        self.assertEqual(runs[0]["fields"][1][1], "Men: 479 -> 478 (-1)\nWomen: 369 -> 371 (+2)")
        self.assertNotIn("hits", runs[0]["fields"][1][1])
        self.assertIn("Men：479 -> 478 (-1)", runs[0]["push_preview"])
        self.assertIn("Women：369 -> 371 (+2)", runs[0]["push_preview"])
        self.assertIn("总计：848 -> 849 (+1)", runs[0]["push_preview"])
        self.assertIn("链接：https://discount.19930216.xyz", runs[0]["push_preview"])

    def test_parse_end_monitor_runs(self):
        sample = """==================== [2026-11-13 09:00:01] Crawler End Started ====================
[END-Montane] Check complete. Prev: 41, Curr: 41, Change: 0
[END-Patagonia] Check complete. Prev: 128, Curr: 128, Change: 0
[END-HOKA ONE ONE] Check complete. Prev: 0, Curr: 16, Change: 16
info: 发送通知到: https://open.feishu.cn/open-apis/bot/v2/hook/xxx
==================== [2026-11-13 09:00:03] Finished (Exit: 0) ===================="""

        runs, _ = web_log.parse_log_runs("end", sample)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "END-HOKA ONE ONE: 0 → 16 (+16)")
        self.assertEqual(runs[0]["fields"][2][1], "Montane: 41\nPatagonia: 128\nHOKA ONE ONE: 16")
        self.assertIn("END-HOKA ONE ONE 产品数量变化通知", runs[0]["push_preview"])
        self.assertIn("变化：0 -> 16 (+16)", runs[0]["push_preview"])
        self.assertIn(
            "链接：https://www.endclothing.com/en-cn/collections/mens-sale?refinementList%5Bvendor%5D%5B0%5D=Hoka",
            runs[0]["push_preview"],
        )

    def test_parse_end_monitor_compact_log_format(self):
        sample = """==================== [2026-11-13 09:30:01] Crawler End Started ====================
[END-Montane] Check complete. 变化: 41 -> 41 (0)
[END-Arcteryx] Check complete. 变化: 15 -> 14 (-1)
info: 发送通知到: https://open.feishu.cn/open-apis/bot/v2/hook/xxx
==================== [2026-11-13 09:30:03] Finished (Exit: 0) ===================="""

        runs, _ = web_log.parse_log_runs("end", sample)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "END-Arcteryx: 15 → 14 (-1)")
        self.assertEqual(runs[0]["fields"][2][1], "Montane: 41\nArcteryx: 14")
        self.assertIn("变化：15 -> 14 (-1)", runs[0]["push_preview"])
        self.assertIn(
            "链接：https://www.endclothing.com/en-cn/collections/mens-sale?refinementList%5Bvendor%5D%5B0%5D=Arcteryx",
            runs[0]["push_preview"],
        )

    def test_parse_thelasthunt_runs(self):
        sample = """==================== [2026-11-13 09:00:01] TheLastHunt Started ====================
info: Notification sent successfully for https://www.thelasthunt.com/c/patagonia
============================================================
🔄 MANUAL REFRESH RESULTS
============================================================

1. Patagonia Products
   URL: https://www.thelasthunt.com/c/patagonia
   Status: ✅ Success
   Previous Count: 144
   Current Count: 145
   Changed: ✅ Yes
   Duration: 1401ms

2. Icebreaker Products
   URL: https://www.thelasthunt.com/c/icebreaker
   Status: ✅ Success
   Previous Count: 62
   Current Count: 62
   Changed: ❌ No
   Duration: 318ms

============================================================
📊 SUMMARY
============================================================
Total URLs: 2
Successful: 2
Failed: 0
Changed: 1
============================================================
==================== [2026-11-13 09:00:03] Finished (Exit: 0) ===================="""

        runs, _ = web_log.parse_log_runs("thelasthunt", sample)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "Patagonia Products: 144 → 145 (+1)")
        self.assertIn("Patagonia Products: 144 -> 145 (+1) [1401ms]", runs[0]["fields"][1][1])
        self.assertIn("变化：144 -> 145 (+1)", runs[0]["push_preview"])

    def test_parse_thelasthunt_compact_log_format(self):
        sample = """==================== [2026-11-13 09:30:01] Scrape Started ====================
info: Notification sent successfully for https://www.thelasthunt.com/c/icebreaker
1. Patagonia Products
   URL: https://www.thelasthunt.com/c/patagonia
   Status: ✅ Success
   变化: 145 -> 145 (0)
   Duration: 347ms

2. Icebreaker Products
   URL: https://www.thelasthunt.com/c/icebreaker
   Status: ✅ Success
   变化: 62 -> 61 (-1)
   Duration: 2017ms
Total URLs: 2
Successful: 2
Failed: 0
Changed: 1
==================== [2026-11-13 09:30:04] Finished (Exit: 0) ===================="""

        runs, _ = web_log.parse_log_runs("thelasthunt", sample)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "Icebreaker Products: 62 → 61 (-1)")
        self.assertIn("Icebreaker Products: 62 -> 61 (-1) [2017ms]", runs[0]["fields"][1][1])
        self.assertIn("变化：62 -> 61 (-1)", runs[0]["push_preview"])

    def test_parse_log_runs_caps_at_100_newest(self):
        blocks = []
        for idx in range(105):
            sec = idx % 60
            minute = idx // 60
            blocks.append(
                f"==================== [2026-11-13 08:{minute:02d}:{sec:02d}] Scrape Started ====================\n"
                f"No product changes detected; skipping file write and notification.\n"
                f"Complete: total={100 + idx} (men={60 + idx}, women=40) changed=False elapsed=1.00s\n"
                f"==================== [2026-11-13 08:{minute:02d}:{sec:02d}] Finished (Exit: 0) ===================="
            )

        runs, _ = web_log.parse_log_runs("end70", "\n".join(blocks))
        self.assertEqual(len(runs), 100)
        self.assertIn("204 items", runs[0]["summary"])
        self.assertIn("105 items", runs[-1]["summary"])

    def test_parse_feishu_sent_tags_across_projects(self):
        end70_sample = """==================== [2026-11-13 11:00:01] Scrape Started ====================
Men: 478 -> 479 (+1)
Women: 369 -> 369 (0)
Total: 847 -> 848 (+1)
Feishu notification sent: status=200, response={"StatusCode":0,"StatusMessage":"success"}
Complete: total=848 (men=479, women=369) changed=True elapsed=1.50s
==================== [2026-11-13 11:00:03] Finished (Exit: 0) ===================="""
        runs, _ = web_log.parse_log_runs("end70", end70_sample)
        self.assertEqual(runs[0]["tag"], "Feishu Sent")
        self.assertIn("HTTP 200", runs[0]["fields"][4][1])

        end_sample = """==================== [2026-11-13 11:00:01] Crawler End Started ====================
[END-Arcteryx] Check complete. 变化: 14 -> 15 (+1)
info: 发送通知到: https://open.feishu.cn/open-apis/bot/v2/hook/e6938e11-3931-4c18-94b1-3658335c5f92
info: 飞书通知发送成功
==================== [2026-11-13 11:00:03] Finished (Exit: 0) ===================="""
        runs, _ = web_log.parse_log_runs("end", end_sample)
        self.assertEqual(runs[0]["tag"], "Feishu Sent")


class AuthAndMonitorConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.end_dir = os.path.join(self.tmp.name, "end")
        self.end_data = os.path.join(self.end_dir, "data")
        self.tlh_dir = os.path.join(self.tmp.name, "tlh")
        self.tlh_data = os.path.join(self.tlh_dir, "data")
        os.makedirs(self.end_data, exist_ok=True)
        os.makedirs(self.tlh_data, exist_ok=True)

        self.end_sites_file = os.path.join(self.end_dir, "sites.json")
        self.tlh_config_file = os.path.join(self.tlh_dir, "config.json")
        with open(self.end_sites_file, "w", encoding="utf-8") as f:
            json.dump([
                {
                    "name": "END-Patagonia",
                    "url": "https://www.endclothing.com/en-cn/collections/mens-sale?refinementList%5Bvendor%5D%5B0%5D=Patagonia",
                }
            ], f)
        with open(self.end_data + "/history-END-Patagonia.json", "w", encoding="utf-8") as f:
            json.dump([{"productCount": 13, "url": "https://www.endclothing.com/en-cn/collections/mens-sale?refinementList%5Bvendor%5D%5B0%5D=Patagonia"}], f)

        with open(self.tlh_config_file, "w", encoding="utf-8") as f:
            json.dump({
                "monitoring_urls": [
                    {
                        "name": "Patagonia Products",
                        "url": "https://www.thelasthunt.com/c/patagonia",
                        "xpath": web_log.TLH_DEFAULT_XPATH,
                    }
                ],
                "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/test-keep",
                "monitoring_interval_minutes": 5,
            }, f)
        with open(self.tlh_data + "/patagonia-products.json", "w", encoding="utf-8") as f:
            json.dump({"product_count": 145, "name": "Patagonia Products"}, f)

        self.orig_end_cfg = dict(web_log.PROJECTS_CONFIG["end"])
        self.orig_tlh_cfg = dict(web_log.PROJECTS_CONFIG["thelasthunt"])
        web_log.PROJECTS_CONFIG["end"]["sites_file"] = self.end_sites_file
        web_log.PROJECTS_CONFIG["end"]["data_dir"] = self.end_data
        web_log.PROJECTS_CONFIG["thelasthunt"]["config_file"] = self.tlh_config_file
        web_log.PROJECTS_CONFIG["thelasthunt"]["data_dir"] = self.tlh_data
        web_log._LOGIN_FAILURES.clear()

        self.env_patch = mock.patch.dict(os.environ, {
            "ADMIN_USERNAME": "admin",
            "ADMIN_PASSWORD": "TestSecretPassword!",
            "SESSION_SECRET": "test-secret-key-123",
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        web_log.PROJECTS_CONFIG["end"].update(self.orig_end_cfg)
        web_log.PROJECTS_CONFIG["thelasthunt"].update(self.orig_tlh_cfg)
        web_log._LOGIN_FAILURES.clear()
        self.tmp.cleanup()

    def test_auth_blocks_unauthenticated_and_allows_after_login(self):
        server = HTTPServer(("127.0.0.1", 0), web_log.MultiProjectLogHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"
            # 未登录访问首页仅返回登录页
            with urllib.request.urlopen(f"{base}/") as resp:
                html = resp.read().decode("utf-8")
                self.assertIn("Sign in as administrator", html)
                self.assertNotIn("window.products", html)

            # 未登录访问所有数据/管理端点均返回 401
            for method, path in [
                ("GET", "/data.js"),
                ("GET", "/api/status?project=end70"),
                ("GET", "/api/monitors?project=end"),
                ("POST", "/api/trigger?project=end"),
                ("POST", "/api/monitors"),
                ("DELETE", "/api/monitors"),
            ]:
                req = urllib.request.Request(f"{base}{path}", method=method, data=b"{}" if method in ("POST", "DELETE") else None)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req)
                self.assertEqual(ctx.exception.code, 401)

            # 连续失败 5 次触发 429 限流
            for idx in range(6):
                req = urllib.request.Request(
                    f"{base}/api/login",
                    data=json.dumps({"username": "admin", "password": "wrong"}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-Real-IP": "1.2.3.4"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req)
                self.assertEqual(ctx.exception.code, 429 if idx >= 5 else 401)

            # 正常登录获取 Cookie 后可访问全部接口
            login_req = urllib.request.Request(
                f"{base}/api/login",
                data=json.dumps({"username": "admin", "password": "TestSecretPassword!"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Real-IP": "9.9.9.9"},
                method="POST",
            )
            with urllib.request.urlopen(login_req) as resp:
                self.assertEqual(resp.status, 200)
                cookie_header = resp.headers.get("Set-Cookie").split(";")[0]
                self.assertIn("dh_session=", cookie_header)

            authed_home = urllib.request.Request(f"{base}/", headers={"Cookie": cookie_header})
            with urllib.request.urlopen(authed_home) as resp:
                self.assertIn("Manage Monitors", resp.read().decode("utf-8"))

            authed_mon = urllib.request.Request(f"{base}/api/monitors?project=end", headers={"Cookie": cookie_header})
            with urllib.request.urlopen(authed_mon) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(body["ok"])
                self.assertEqual(len(body["items"]), 1)
                self.assertEqual(body["items"][0]["count"], 13)
        finally:
            server.shutdown()
            server.server_close()

    def test_end_and_tlh_add_delete_and_cascade_cleanup(self):
        # 禁止非白名单域名
        with self.assertRaises(ValueError):
            web_log.add_monitor_item("end", {"name": "END-Evil", "url": "https://evil.com/x"})
        with self.assertRaises(ValueError):
            web_log.add_monitor_item("thelasthunt", {"name": "Evil", "url": "http://www.thelasthunt.com/c/x"})

        # End 新增全站品牌与删除清理历史文件
        items = web_log.add_monitor_item("end", {
            "name": "END-Nike",
            "url": "https://www.endclothing.com/en-cn/collections/all?refinementList%5Bvendor%5D%5B0%5D=Nike",
            "vendor": "Nike",
            "scope": "all",
        })
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["scope"], "All Store")

        items, removed = web_log.delete_monitor_item("end", name="END-Patagonia")
        self.assertEqual(removed, 1)
        self.assertEqual(len(items), 1)
        self.assertFalse(os.path.exists(os.path.join(self.end_data, "history-END-Patagonia.json")))

        # TLH 新增、保留 webhook 字段、删除联动清理缓存
        tlh_items = web_log.add_monitor_item("thelasthunt", {
            "name": "Salomon Products",
            "url": "https://www.thelasthunt.com/c/salomon",
        })
        self.assertEqual(len(tlh_items), 2)
        with open(self.tlh_config_file, "r", encoding="utf-8") as f:
            raw_tlh = json.load(f)
        self.assertEqual(raw_tlh["feishu_webhook"], "https://open.feishu.cn/open-apis/bot/v2/hook/test-keep")
        self.assertEqual(raw_tlh["monitoring_urls"][1]["xpath"], web_log.TLH_DEFAULT_XPATH)

        tlh_items, removed = web_log.delete_monitor_item("thelasthunt", name="Patagonia Products")
        self.assertEqual(removed, 1)
        self.assertEqual(len(tlh_items), 1)
        self.assertFalse(os.path.exists(os.path.join(self.tlh_data, "patagonia-products.json")))

    def test_preview_end_and_tlh_by_brand_and_url(self):
        def fake_post_json(url, payload, headers=None, timeout=10):
            req0 = payload["requests"][0]
            if req0["indexName"] == web_log.END_ALGOLIA_INDEX:
                if "facets=" in req0["params"]:
                    return {"results": [{"facets": {"vendor": {"Hoka": 16, "Patagonia": 78, "Nike": 531}}}]}
                return {"results": [{"nbHits": 16}]}
            if req0["indexName"] == web_log.TLH_ALGOLIA_INDEX:
                if "facets=" in req0["params"]:
                    return {"results": [{"facets": {"attributes.brand_name": {"Salomon": 45, "Arc'teryx": 30}}}]}
                return {"results": [{"nbHits": 45}]}
            raise AssertionError(f"Unexpected index: {req0}")

        with mock.patch("web_log._post_json", side_effect=fake_post_json):
            end_brand = web_log.preview_monitor_target("end", "hoka")
            self.assertTrue(end_brand["valid"])
            self.assertEqual(end_brand["vendor"], "Hoka")
            self.assertEqual(end_brand["matched_count"], 16)
            self.assertEqual(end_brand["scope"], "all")
            self.assertIn("/collections/all?", end_brand["url"])

            end_miss = web_log.preview_monitor_target("end", "hok")
            self.assertFalse(end_miss["valid"])
            self.assertEqual(end_miss["candidates"][0]["input"], "Hoka")

            tlh_brand = web_log.preview_monitor_target("thelasthunt", "Salomon")
            self.assertTrue(tlh_brand["valid"])
            self.assertEqual(tlh_brand["url"], "https://www.thelasthunt.com/c/salomon")
            self.assertEqual(tlh_brand["matched_count"], 45)

            tlh_url = web_log.preview_monitor_target(
                "thelasthunt",
                "https://www.thelasthunt.com/c/all?brand_name=Salomon&discounted_percent=50%3A",
            )
            self.assertTrue(tlh_url["valid"])
            self.assertEqual(tlh_url["matched_count"], 45)


if __name__ == "__main__":
    unittest.main()
