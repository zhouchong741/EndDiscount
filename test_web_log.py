import unittest

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
DingTalk notification sent: status=200, response={"errcode":0,"errmsg":"ok"}
Complete: total=847 (men=478, women=369) changed=True elapsed=2.25s
==================== [2026-11-13 09:20:04] Finished (Exit: 0) ===================="""

        runs, avg = web_log.parse_log_runs("end70", sample)
        self.assertEqual(len(runs), 3)
        # 倒序：最新在第一条
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "848 → 847 (-1 items)")
        self.assertIn("总计：847（-1）", runs[0]["push_preview"])
        self.assertEqual(runs[1]["status"], "ok")
        self.assertEqual(runs[1]["summary"], "848 items (478M / 370W)")
        self.assertEqual(runs[2]["status"], "err")
        self.assertIn("UnboundLocalError", runs[2]["summary"])
        self.assertNotEqual(avg, "--")

    def test_parse_end_monitor_runs(self):
        sample = """==================== [2026-11-13 09:00:01] Crawler End Started ====================
[END-Montane] Check complete. Prev: 41, Curr: 41, Change: 0
[END-Patagonia] Check complete. Prev: 128, Curr: 128, Change: 0
[END-HOKA ONE ONE] Check complete. Prev: 0, Curr: 16, Change: 16
info: 发送通知到: https://oapi.dingtalk.com/robot/send?access_token=xxx
==================== [2026-11-13 09:00:03] Finished (Exit: 0) ===================="""

        runs, _ = web_log.parse_log_runs("end", sample)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "push")
        self.assertEqual(runs[0]["summary"], "END-HOKA ONE ONE: 0 → 16 (+16)")
        self.assertIn("END-HOKA ONE ONE 产品数量变化通知", runs[0]["push_preview"])
        self.assertIn("之前：0 现在：16", runs[0]["push_preview"])

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
        self.assertIn("之前：144 现在：145", runs[0]["push_preview"])


if __name__ == "__main__":
    unittest.main()
