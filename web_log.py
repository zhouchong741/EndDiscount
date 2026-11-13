#!/usr/bin/env python3
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

SHANGHAI_TZ = timezone(timedelta(hours=8))
ISO_UTC_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z")
RUN_START_RE = re.compile(r"^=+\s*\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s+.+?\s+Started\s*=+$")
RUN_END_RE = re.compile(r"^=+\s*\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s+Finished\s*\(Exit:\s*(-?\d+)\)\s*=+$")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8088

PROJECTS_CONFIG = {
    "end70": {
        "id": "end70",
        "name": "End70New",
        "badge": "Algolia API",
        "log_file": "/var/log/enddiscount.log",
        "run_script": "/opt/crawler/EndDiscount/run_cron.sh",
        "data_file": os.path.join(BASE_DIR, "endclothing_70off.json"),
        "cron_schedule": "Every 5m",
        "default_sub": "60% ~ 70% Off",
    },
    "end": {
        "id": "end",
        "name": "End Monitor",
        "badge": "Node.js",
        "log_file": "/var/log/crawler-end.log",
        "run_script": "/opt/crawler/End/run_cron.sh",
        "data_dir": "/opt/crawler/End/data",
        "cron_schedule": "Every 30m",
        "default_sub": "Brand Monitor",
    },
    "thelasthunt": {
        "id": "thelasthunt",
        "name": "TheLastHunt",
        "badge": "Node.js",
        "log_file": "/var/log/crawler-thelasthunt.log",
        "run_script": "/opt/crawler/TheLastHunt/run_cron.sh",
        "data_dir": "/opt/crawler/TheLastHunt/data",
        "cron_schedule": "Every 30m",
        "default_sub": "Outdoor Brands",
    },
}


def convert_utc_iso_to_shanghai(text):
    """将日志中的 UTC ISO 时间戳统一转换为 Asia/Shanghai (UTC+8) 字符串。"""
    def _repl(m):
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            dt_sh = dt.astimezone(SHANGHAI_TZ)
            ms = f".{m.group(3)}" if m.group(3) else ""
            return f"{dt_sh.strftime('%Y-%m-%d %H:%M:%S')}{ms}"
        except Exception:
            return m.group(0)
    return ISO_UTC_RE.sub(_repl, text)


def get_log_tail(log_path, lines=1200):
    """读取指定日志文件末尾 N 行并转换时区。"""
    if not os.path.exists(log_path):
        return f"Log file {log_path} not found."
    try:
        res = subprocess.run(
            ["tail", "-n", str(lines), log_path],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return convert_utc_iso_to_shanghai(res.stdout) if res.stdout else "(Log is empty)"
    except Exception as e:
        return f"Failed to read log: {e}"


def _calc_duration_sec(start_str, end_str):
    if not start_str or not end_str:
        return None
    try:
        t1 = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        t2 = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
        return max(0.0, (t2 - t1).total_seconds())
    except Exception:
        return None


def _split_log_blocks(log_text):
    """按 Started / Finished 分界将日志文本切分为单次运行块列表（保留原始时间戳顺序）。"""
    blocks = []
    current = None
    for line in log_text.splitlines():
        m_start = RUN_START_RE.match(line.strip())
        if m_start:
            if current is not None:
                blocks.append(current)
            current = {
                "start_time": m_start.group(1),
                "end_time": None,
                "exit_code": None,
                "lines": [line],
            }
            continue

        if current is None:
            continue

        current["lines"].append(line)
        m_end = RUN_END_RE.match(line.strip())
        if m_end:
            current["end_time"] = m_end.group(1)
            current["exit_code"] = int(m_end.group(2))
            blocks.append(current)
            current = None

    if current is not None:
        blocks.append(current)
    return blocks


def _format_signed_diff(diff):
    if diff > 0:
        return f"+{diff}"
    if diff < 0:
        return f"-{abs(diff)}"
    return "0"


def _format_category_line(label, prev_val, curr_val, diff_str):
    if prev_val is not None and diff_str is not None:
        return f"{label}: {prev_val} -> {curr_val} ({diff_str})"
    return f"{label}: {curr_val}"


def _format_preview_line(prev_val, curr_val, diff_str):
    if prev_val is not None and diff_str is not None:
        return f"{prev_val} -> {curr_val} ({diff_str})"
    if diff_str is not None:
        return f"{curr_val}（{diff_str}）"
    return str(curr_val)


def _parse_end70_block(block, run_id, prev_counts=None):
    raw = "\n".join(block["lines"])
    start_time = block["start_time"]
    end_time = block["end_time"] or start_time
    exit_code = block["exit_code"]

    m_men_line = re.search(r"(?m)^Men:\s*(\d+)\s*->\s*(\d+)\s*\(([^)]+)\)", raw)
    m_women_line = re.search(r"(?m)^Women:\s*(\d+)\s*->\s*(\d+)\s*\(([^)]+)\)", raw)
    m_total_line = re.search(r"(?m)^Total:\s*(\d+)\s*->\s*(\d+)\s*\(([^)]+)\)", raw)
    m_chg_line = re.search(
        r"变化:\s*Men\s+(\d+)\s*->\s*(\d+)\s*\(([^)]+)\)\s*\|\s*Women\s+(\d+)\s*->\s*(\d+)\s*\(([^)]+)\)\s*\|\s*Total\s+(\d+)\s*->\s*(\d+)\s*\(([^)]+)\)",
        raw,
    )
    m_prev = re.search(r"Loaded (\d+) existing products", raw)
    m_comp = re.search(r"Complete: total=(\d+) \(men=(\d+), women=(\d+)\) changed=(True|False) elapsed=([\d.]+)s", raw)
    m_link = re.search(r"(?m)^Link:\s*(\S+)", raw)
    m_push = re.search(r"DingTalk notification sent: status=(\d+), response=(.+)", raw)
    m_fail = re.search(r"Scrape failed:\s*(.+)", raw)
    site_url = m_link.group(1).strip() if m_link else "https://discount.19930216.xyz"

    if m_total_line:
        prev_total = int(m_total_line.group(1))
    elif m_chg_line:
        prev_total = int(m_chg_line.group(7))
    elif m_prev:
        prev_total = int(m_prev.group(1))
    else:
        prev_total = None

    dur_val = float(m_comp.group(5)) if m_comp else _calc_duration_sec(start_time, block["end_time"])
    dur_str = f"{dur_val:.2f}s" if dur_val is not None else "--"

    if exit_code not in (None, 0) or m_fail:
        err_msg = m_fail.group(1).strip() if m_fail else f"Exited with code {exit_code}"
        return {
            "id": run_id,
            "time": start_time[11:],
            "full_time": start_time,
            "status": "err",
            "summary": err_msg[:72],
            "tag": "Error",
            "dur": dur_str,
            "dur_val": dur_val,
            "fields": [
                ["Status", f"Exit {exit_code if exit_code is not None else 1} · Scrape Failed"],
                ["Exception", err_msg],
                ["Time", f"{start_time} -> {end_time}"],
            ],
            "push_preview": None,
            "raw": raw,
        }

    if not m_comp:
        return {
            "id": run_id,
            "time": start_time[11:],
            "full_time": start_time,
            "status": "ok",
            "summary": "Running / Partial log",
            "tag": "Running" if exit_code is None else "Done",
            "dur": dur_str,
            "dur_val": dur_val,
            "fields": [["Status", "In progress or incomplete block"], ["Start", start_time]],
            "push_preview": None,
            "raw": raw,
        }

    total, men, women = int(m_comp.group(1)), int(m_comp.group(2)), int(m_comp.group(3))
    changed = m_comp.group(4) == "True"

    if m_men_line and m_women_line and m_total_line:
        prev_men = int(m_men_line.group(1))
        prev_women = int(m_women_line.group(1))
        men_diff_str = m_men_line.group(3).strip()
        women_diff_str = m_women_line.group(3).strip()
        total_diff_str = m_total_line.group(3).strip()
    elif m_chg_line:
        prev_men = int(m_chg_line.group(1))
        prev_women = int(m_chg_line.group(4))
        men_diff_str = m_chg_line.group(3).strip()
        women_diff_str = m_chg_line.group(6).strip()
        total_diff_str = m_chg_line.group(9).strip()
    elif prev_counts and (prev_total is None or prev_total == prev_counts[0]):
        prev_total = prev_counts[0]
        prev_men, prev_women = prev_counts[1], prev_counts[2]
        men_diff_str = _format_signed_diff(men - prev_men)
        women_diff_str = _format_signed_diff(women - prev_women)
        total_diff_str = _format_signed_diff(total - prev_total)
    else:
        prev_men = prev_women = None
        men_diff_str = women_diff_str = None
        total_diff_str = _format_signed_diff(total - prev_total) if prev_total is not None else "0"

    breakdown = "\n".join([
        _format_category_line("Men", prev_men, men, men_diff_str),
        _format_category_line("Women", prev_women, women, women_diff_str),
    ])

    if changed or m_push:
        summary = f"{total} items ({men}M / {women}W)"
        if prev_total is not None and prev_total != total:
            diff = total - prev_total
            diff_str = _format_signed_diff(diff)
            summary = f"{prev_total} → {total} ({diff_str} items)"
        else:
            diff_str = total_diff_str if (total_diff_str and total_diff_str != "0") else "信息/结构更新"

        fields = [
            ["Status", "Exit 0 · Changed & Pushed"],
            ["Category Breakdown", breakdown],
            ["Change Summary", f"Total: {prev_total if prev_total is not None else total} -> {total} ({diff_str})"],
            ["Link", site_url],
        ]
        if m_push:
            fields.append(["Webhook Status", f"HTTP {m_push.group(1)} · {m_push.group(2).strip()}"])
        push_preview = (
            f"### End70 产品数量变化通知\n"
            f"时间：{end_time.replace('-', '/')}\n"
            f"Men：{_format_preview_line(prev_men, men, men_diff_str)}\n"
            f"Women：{_format_preview_line(prev_women, women, women_diff_str)}\n"
            f"总计：{_format_preview_line(prev_total, total, diff_str)}\n"
            f"链接：{site_url}"
        )
        return {
            "id": run_id,
            "time": start_time[11:],
            "full_time": start_time,
            "status": "push",
            "summary": summary,
            "tag": "DingTalk Sent" if m_push else "Changed",
            "dur": dur_str,
            "dur_val": dur_val,
            "fields": fields,
            "push_preview": push_preview,
            "raw": raw,
            "_counts": (total, men, women),
        }

    return {
        "id": run_id,
        "time": start_time[11:],
        "full_time": start_time,
        "status": "ok",
        "summary": f"{total} items ({men}M / {women}W)",
        "tag": "No change",
        "dur": dur_str,
        "dur_val": dur_val,
        "fields": [
            ["Status", "Exit 0 · Unchanged (Skipped file write & alert)"],
            ["Category Breakdown", breakdown],
            ["Change Summary", f"Total: {prev_total if prev_total is not None else total} -> {total} ({total_diff_str}) · Duration: {dur_str}"],
            ["Link", site_url],
        ],
        "push_preview": None,
        "raw": raw,
        "_counts": (total, men, women),
    }


def _get_end_brand_url(brand_name):
    """获取 End 品牌对应的监控链接，优先从历史数据读取并回退标准 URL。"""
    data_dir = PROJECTS_CONFIG["end"].get("data_dir")
    if data_dir:
        hist_file = os.path.join(data_dir, f"history-END-{brand_name}.json")
        if os.path.exists(hist_file):
            try:
                with open(hist_file, "r", encoding="utf-8") as f:
                    records = json.load(f)
                if isinstance(records, list) and records and isinstance(records[-1], dict):
                    url = records[-1].get("url")
                    if url:
                        return url
            except Exception:
                pass
    vendor = "Hoka" if brand_name == "HOKA ONE ONE" else brand_name
    return f"https://www.endclothing.com/en-cn/collections/mens-sale?refinementList%5Bvendor%5D%5B0%5D={quote(vendor, safe='')}"


def _parse_end_block(block, run_id):
    raw = "\n".join(block["lines"])
    start_time = block["start_time"]
    end_time = block["end_time"] or start_time
    exit_code = block["exit_code"]
    dur_val = _calc_duration_sec(start_time, block["end_time"])
    dur_str = f"{dur_val:.2f}s" if dur_val is not None else "--"

    brands = re.findall(
        r"\[END-([^\]]+)\] Check complete\. 变化:\s*(\d+)\s*->\s*(\d+)\s*\(([+-]?\d+)\)",
        raw,
    )
    if not brands:
        brands = re.findall(
            r"\[END-([^\]]+)\] Check complete\. Prev: (\d+), Curr: (\d+), Change: ([+-]?\d+)",
            raw,
        )
    has_err_line = bool(re.search(r"(?m)^error:", raw))
    has_notify = "发送通知到:" in raw or any(int(c) != 0 for _, _, _, c in brands)

    if exit_code not in (None, 0) or has_err_line:
        err_match = re.search(r"(?m)^error:\s*(.+)", raw)
        err_msg = err_match.group(1).strip() if err_match else f"Exited with code {exit_code}"
        return {
            "id": run_id,
            "time": start_time[11:],
            "full_time": start_time,
            "status": "err",
            "summary": err_msg[:72],
            "tag": "Error",
            "dur": dur_str,
            "dur_val": dur_val,
            "fields": [["Status", f"Exit {exit_code} · Error"], ["Message", err_msg]],
            "push_preview": None,
            "raw": raw,
        }

    # 每个品牌单独一行，方便查看
    brand_lines_str = "\n".join(f"{name}: {curr}" for name, _, curr, _ in brands) if brands else "No brand details parsed"

    changed_brands = [(name, int(prev), int(curr), int(chg)) for name, prev, curr, chg in brands if int(chg) != 0]

    if has_notify and changed_brands:
        first_name, f_prev, f_curr, f_chg = changed_brands[0]
        sign = "+" if f_chg > 0 else ""
        summary = f"END-{first_name}: {f_prev} → {f_curr} ({sign}{f_chg})"
        if len(changed_brands) > 1:
            summary += f" (+{len(changed_brands) - 1} more)"

        change_lines = "\n".join(
            f"END-{n}: {p} -> {c} ({'+' if g > 0 else ''}{g})" for n, p, c, g in changed_brands
        )
        push_blocks = []
        for n, p, c, g in changed_brands:
            csign = "+" if g > 0 else ""
            push_blocks.append(
                f"### END-{n} 产品数量变化通知\n"
                f"时间：{end_time.replace('-', '/')}\n"
                f"变化：{p} -> {c} ({csign}{g})\n"
                f"链接：{_get_end_brand_url(n)}"
            )

        return {
            "id": run_id,
            "time": start_time[11:],
            "full_time": start_time,
            "status": "push",
            "summary": summary,
            "tag": "DingTalk Sent",
            "dur": dur_str,
            "dur_val": dur_val,
            "fields": [
                ["Status", f"Exit 0 · {len(brands)} Brands Checked ({len(changed_brands)} Changed)"],
                ["Changed Brands", change_lines],
                ["All Brands", brand_lines_str],
            ],
            "push_preview": "\n\n".join(push_blocks),
            "raw": raw,
        }

    total_items = sum(int(curr) for _, _, curr, _ in brands)
    return {
        "id": run_id,
        "time": start_time[11:],
        "full_time": start_time,
        "status": "ok",
        "summary": f"{len(brands)}/{len(brands)} Brands 200 OK · {total_items} items" if brands else "Completed",
        "tag": "No change",
        "dur": dur_str,
        "dur_val": dur_val,
        "fields": [
            ["Status", f"Exit {exit_code if exit_code is not None else 0} · {len(brands)} Brands Healthy"],
            ["Brand Counts", brand_lines_str],
            ["Duration", dur_str],
        ],
        "push_preview": None,
        "raw": raw,
    }


def _parse_tlh_block(block, run_id):
    raw = "\n".join(block["lines"])
    start_time = block["start_time"]
    end_time = block["end_time"] or start_time
    exit_code = block["exit_code"]
    dur_val = _calc_duration_sec(start_time, block["end_time"])
    dur_str = f"{dur_val:.2f}s" if dur_val is not None else "--"

    cat_pattern_new = re.compile(
        r"\d+\.\s+([^\n]+)\n\s+URL:\s+(\S+)\n\s+Status:\s+([^\n]+)\n\s+变化:\s+(\d+)\s*->\s*(\d+)\s*\(([+-]?\d+)\)\n\s+Duration:\s+([^\n]+)"
    )
    cat_pattern_old = re.compile(
        r"\d+\.\s+([^\n]+)\n\s+URL:\s+(\S+)\n\s+Status:\s+([^\n]+)\n\s+Previous Count:\s+(\d+)\n\s+Current Count:\s+(\d+)\n\s+Changed:\s+([^\n]+)\n\s+Duration:\s+([^\n]+)"
    )
    cats_new = cat_pattern_new.findall(raw)
    cats_old = [] if cats_new else cat_pattern_old.findall(raw)
    parsed_cats = [
        (name, url, st, int(prev), int(curr), (int(prev) != int(curr)) or (int(chg_val) != 0), cdur)
        for name, url, st, prev, curr, chg_val, cdur in cats_new
    ] or [
        (name, url, st, int(prev), int(curr), ("Yes" in chg) or (int(prev) != int(curr)), cdur)
        for name, url, st, prev, curr, chg, cdur in cats_old
    ]
    notify_urls = re.findall(r"Notification sent successfully for (\S+)", raw)

    m_sum = re.search(r"Total URLs:\s*(\d+)\s*\n\s*Successful:\s*(\d+)\s*\n\s*Failed:\s*(\d+)\s*\n\s*Changed:\s*(\d+)", raw)
    total_urls = int(m_sum.group(1)) if m_sum else len(parsed_cats)
    succ_urls = int(m_sum.group(2)) if m_sum else len(parsed_cats)
    failed_urls = int(m_sum.group(3)) if m_sum else 0

    if exit_code not in (None, 0) or failed_urls > 0:
        return {
            "id": run_id,
            "time": start_time[11:],
            "full_time": start_time,
            "status": "err",
            "summary": f"Failed: {failed_urls}/{total_urls} URLs failed",
            "tag": "Error",
            "dur": dur_str,
            "dur_val": dur_val,
            "fields": [["Status", f"Exit {exit_code} · Failed URLs: {failed_urls}"]],
            "push_preview": None,
            "raw": raw,
        }

    total_items = sum(c_i for _, _, _, _, c_i, _, _ in parsed_cats)
    cat_lines = []
    changed_cats = []
    for name, url, st, p_i, c_i, is_chg, cdur in parsed_cats:
        if is_chg:
            diff = c_i - p_i
            sign = "+" if diff > 0 else ""
            cat_lines.append(f"{name.strip()}: {p_i} -> {c_i} ({sign}{diff}) [{cdur.strip()}]")
            changed_cats.append((name.strip(), url, p_i, c_i, diff))
        else:
            cat_lines.append(f"{name.strip()}: {c_i} [{cdur.strip()}]")

    cat_breakdown_str = "\n".join(cat_lines) if cat_lines else "No category breakdown parsed"

    if changed_cats or notify_urls:
        if changed_cats:
            n0, _, p0, c0, d0 = changed_cats[0]
            s0 = "+" if d0 > 0 else ""
            summary = f"{n0}: {p0} → {c0} ({s0}{d0})"
            if len(changed_cats) > 1:
                summary += f" (+{len(changed_cats) - 1} more)"
        else:
            summary = f"{succ_urls}/{total_urls} URLs · Alert Sent"

        push_blocks = []
        for n, url, p_i, c_i, diff in changed_cats:
            sign = "+" if diff > 0 else ""
            push_blocks.append(
                f"### {n} 产品数量变化通知\n"
                f"时间：{end_time.replace('-', '/')}\n"
                f"变化：{p_i} -> {c_i} ({sign}{diff})\n"
                f"链接：{url}"
            )
        fields = [
            ["Status", f"Exit 0 · {succ_urls}/{total_urls} URLs Healthy (Total: {total_items})"],
            ["Category Breakdown", cat_breakdown_str],
        ]
        if notify_urls:
            fields.append(["Webhook Sent", "\n".join(notify_urls)])

        return {
            "id": run_id,
            "time": start_time[11:],
            "full_time": start_time,
            "status": "push",
            "summary": summary,
            "tag": "DingTalk Sent" if notify_urls else "Changed",
            "dur": dur_str,
            "dur_val": dur_val,
            "fields": fields,
            "push_preview": "\n\n".join(push_blocks) if push_blocks else None,
            "raw": raw,
        }

    return {
        "id": run_id,
        "time": start_time[11:],
        "full_time": start_time,
        "status": "ok",
        "summary": f"{succ_urls}/{total_urls} URLs · {total_items} items" if parsed_cats else "Completed",
        "tag": "No change",
        "dur": dur_str,
        "dur_val": dur_val,
        "fields": [
            ["Status", f"Exit 0 · {succ_urls}/{total_urls} URLs Healthy"],
            ["Category Breakdown", cat_breakdown_str],
            ["Total Items", f"{total_items} · Duration: {dur_str}"],
        ],
        "push_preview": None,
        "raw": raw,
    }


def parse_log_runs(project_key, log_text, max_runs=100):
    """将日志正文解析为结构化的倒序运行列表及平均耗时。"""
    blocks = _split_log_blocks(log_text)
    if not blocks:
        return [], "--"

    recent_blocks = blocks[-max_runs:]
    # 若存在更早的运行块，提取最后一次完整运行的计数作为首条基线
    prev_end70_counts = None
    if project_key == "end70" and len(blocks) > max_runs:
        for older_block in reversed(blocks[:-max_runs]):
            m_old = re.search(
                r"Complete: total=(\d+) \(men=(\d+), women=(\d+)\)",
                "\n".join(older_block["lines"]),
            )
            if m_old:
                prev_end70_counts = (int(m_old.group(1)), int(m_old.group(2)), int(m_old.group(3)))
                break

    parser_map = {
        "end": _parse_end_block,
        "thelasthunt": _parse_tlh_block,
    }

    parsed_items = []
    total_recent = len(recent_blocks)
    for forward_idx, block in enumerate(recent_blocks):
        rev_idx = total_recent - 1 - forward_idx
        run_id = f"{project_key}-{block['start_time'].replace(' ', '_').replace(':', '')}-{rev_idx}"
        if project_key == "end70":
            item = _parse_end70_block(block, run_id, prev_counts=prev_end70_counts)
            if "_counts" in item:
                prev_end70_counts = item.pop("_counts")
        else:
            item = parser_map.get(project_key, _parse_end_block)(block, run_id)
        parsed_items.append(item)

    runs = list(reversed(parsed_items))
    durations = [r["dur_val"] for r in runs if r.get("dur_val") is not None]

    avg_dur = f"{(sum(durations) / len(durations)):.2f}s" if durations else "--"
    return runs, avg_dur


def _get_project_counts():
    """快速汇总 3 个项目的当前监控对象数量用于顶栏 Tab 徽章。"""
    counts = {"end70": "--", "end": "--", "thelasthunt": "--"}
    end70_file = PROJECTS_CONFIG["end70"]["data_file"]
    if os.path.exists(end70_file):
        try:
            with open(end70_file, "r", encoding="utf-8") as f:
                counts["end70"] = str(len(json.load(f)))
        except Exception:
            pass
    for key in ("end", "thelasthunt"):
        d = PROJECTS_CONFIG[key].get("data_dir")
        if d and os.path.exists(d):
            try:
                counts[key] = str(len([f for f in os.listdir(d) if f.endswith(".json")]))
            except Exception:
                pass
    return counts


def get_project_status(project_key):
    """汇总单个项目的状态、单行摘要数据及结构化运行流水。"""
    cfg = PROJECTS_CONFIG.get(project_key, PROJECTS_CONFIG["end70"])
    tail_lines_map = {"end70": 1500, "end": 10000, "thelasthunt": 4500}
    log_tail = get_log_tail(cfg["log_file"], tail_lines_map.get(project_key, 4500))
    runs, avg_duration = parse_log_runs(cfg["id"], log_tail, max_runs=100)

    now_sh = datetime.now(SHANGHAI_TZ)
    server_time = now_sh.strftime("%Y-%m-%d %H:%M:%S")
    server_ts_ms = int(now_sh.timestamp() * 1000)

    total = 0
    last_mod = "N/A"
    detail_stats = ""

    if project_key == "end70":
        data_file = cfg.get("data_file")
        if data_file and os.path.exists(data_file):
            try:
                mtime = os.path.getmtime(data_file)
                last_mod = datetime.fromtimestamp(mtime, SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                with open(data_file, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    total = len(items)
                    men = sum(1 for x in items if x.get("type") == "men")
                    women = sum(1 for x in items if x.get("type") == "women")
                detail_stats = f"Men {men} · Women {women} ({total})"
            except Exception:
                pass
        else:
            detail_stats = "Initializing..."
    else:
        data_dir = cfg.get("data_dir")
        if data_dir and os.path.exists(data_dir):
            try:
                files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.endswith(".json")]
                if files:
                    latest_file = max(files, key=os.path.getmtime)
                    mtime = os.path.getmtime(latest_file)
                    last_mod = datetime.fromtimestamp(mtime, SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    total = len(files)
                    detail_stats = f"{total} {'Brands' if project_key == 'end' else 'Categories'}"
            except Exception:
                pass
        if runs:
            latest_summary = runs[0].get("summary")
            if latest_summary:
                detail_stats = latest_summary

    if last_mod == "N/A" and os.path.exists(cfg["log_file"]):
        try:
            mtime = os.path.getmtime(cfg["log_file"])
            last_mod = datetime.fromtimestamp(mtime, SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    if not detail_stats:
        detail_stats = cfg["default_sub"]

    has_recent_err = bool(runs and runs[0]["status"] == "err")

    return {
        "project": cfg["id"],
        "name": cfg["name"],
        "badge": cfg["badge"],
        "log_file": cfg["log_file"],
        "total": total,
        "detail_stats": detail_stats,
        "data_updated_at": last_mod,
        "cron_schedule": cfg["cron_schedule"],
        "avg_duration": avg_duration,
        "healthy": not has_recent_err,
        "server_time": server_time,
        "server_ts_ms": server_ts_ms,
        "project_counts": _get_project_counts(),
        "runs": runs,
    }


def trigger_scrape_background(project_key):
    cfg = PROJECTS_CONFIG.get(project_key)
    if not cfg:
        return False, "Unknown project"

    script = cfg["run_script"]
    if not os.path.exists(script):
        local_script = os.path.join(BASE_DIR, "run_cron.sh")
        if os.path.exists(local_script) and project_key == "end70":
            script = local_script
        else:
            return False, f"Script not found: {script}"

    def _run():
        try:
            subprocess.run(["/bin/bash", script], timeout=90)
        except Exception as err:
            print(f"Trigger error on {project_key}: {err}", flush=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"{cfg['name']} triggered"


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light dark">
  <script>
    try {
      document.querySelector('meta[name="color-scheme"]').content = localStorage.getItem("color-scheme") ?? "light dark";
    } catch (e) {}
  </script>
  <title>Discount Hub</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {
      --bg-canvas-light: #f8fafc;
      --bg-canvas-dark: #090d16;
      --bg-surface-light: rgba(255, 255, 255, 0.92);
      --bg-surface-dark: rgba(15, 23, 42, 0.82);
      --bg-elevated-light: #ffffff;
      --bg-elevated-dark: #111827;
      --bg-pill-light: #f1f5f9;
      --bg-pill-dark: #1e293b;
      --bg-nav-light: rgba(255, 255, 255, 0.85);
      --bg-nav-dark: rgba(9, 13, 22, 0.85);
      --bg-hover-light: rgba(241, 245, 249, 0.7);
      --bg-hover-dark: rgba(30, 41, 59, 0.45);

      --text-primary-light: #0f172a;
      --text-primary-dark: #f1f5f9;
      --text-secondary-light: #475569;
      --text-secondary-dark: #94a3b8;
      --text-muted-light: #64748b;
      --text-muted-dark: #64748b;

      --border-subtle-light: rgba(226, 232, 240, 0.95);
      --border-subtle-dark: rgba(51, 65, 85, 0.6);
      --border-strong-light: #cbd5e1;
      --border-strong-dark: rgba(56, 189, 248, 0.35);

      --accent-cyan-light: #0284c7;
      --accent-cyan-dark: #38bdf8;
      --accent-emerald-light: #059669;
      --accent-emerald-dark: #34d399;
      --accent-price-light: #e11d48;
      --accent-price-dark: #fb7185;
      --card-shadow-light: 0 4px 20px -2px rgba(15, 23, 42, 0.05);
      --card-shadow-dark: 0 8px 28px -4px rgba(0, 0, 0, 0.5);

      --bg-canvas: var(--bg-canvas-light);
      --bg-surface: var(--bg-surface-light);
      --bg-elevated: var(--bg-elevated-light);
      --bg-pill: var(--bg-pill-light);
      --bg-nav: var(--bg-nav-light);
      --bg-hover: var(--bg-hover-light);
      --text-primary: var(--text-primary-light);
      --text-secondary: var(--text-secondary-light);
      --text-muted: var(--text-muted-light);
      --border-subtle: var(--border-subtle-light);
      --border-strong: var(--border-strong-light);
      --accent-cyan: var(--accent-cyan-light);
      --accent-emerald: var(--accent-emerald-light);
      --accent-price: var(--accent-price-light);
      --card-shadow: var(--card-shadow-light);

      @media (prefers-color-scheme: dark) {
        --bg-canvas: var(--bg-canvas-dark);
        --bg-surface: var(--bg-surface-dark);
        --bg-elevated: var(--bg-elevated-dark);
        --bg-pill: var(--bg-pill-dark);
        --bg-nav: var(--bg-nav-dark);
        --bg-hover: var(--bg-hover-dark);
        --text-primary: var(--text-primary-dark);
        --text-secondary: var(--text-secondary-dark);
        --text-muted: var(--text-muted-dark);
        --border-subtle: var(--border-subtle-dark);
        --border-strong: var(--border-strong-dark);
        --accent-cyan: var(--accent-cyan-dark);
        --accent-emerald: var(--accent-emerald-dark);
        --accent-price: var(--accent-price-dark);
        --card-shadow: var(--card-shadow-dark);
      }

      @supports (color: light-dark(white, black)) {
        --bg-canvas: light-dark(var(--bg-canvas-light), var(--bg-canvas-dark));
        --bg-surface: light-dark(var(--bg-surface-light), var(--bg-surface-dark));
        --bg-elevated: light-dark(var(--bg-elevated-light), var(--bg-elevated-dark));
        --bg-pill: light-dark(var(--bg-pill-light), var(--bg-pill-dark));
        --bg-nav: light-dark(var(--bg-nav-light), var(--bg-nav-dark));
        --bg-hover: light-dark(var(--bg-hover-light), var(--bg-hover-dark));
        --text-primary: light-dark(var(--text-primary-light), var(--text-primary-dark));
        --text-secondary: light-dark(var(--text-secondary-light), var(--text-secondary-dark));
        --text-muted: light-dark(var(--text-muted-light), var(--text-muted-dark));
        --border-subtle: light-dark(var(--border-subtle-light), var(--border-subtle-dark));
        --border-strong: light-dark(var(--border-strong-light), var(--border-strong-dark));
        --accent-cyan: light-dark(var(--accent-cyan-light), var(--accent-cyan-dark));
        --accent-emerald: light-dark(var(--accent-emerald-light), var(--accent-emerald-dark));
        --accent-price: light-dark(var(--accent-price-light), var(--accent-price-dark));
        --card-shadow: light-dark(var(--card-shadow-light), var(--card-shadow-dark));
      }
    }

    body {
      background-color: var(--bg-canvas);
      color: var(--text-primary);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      transition: background-color 0.2s ease, color 0.2s ease;
    }

    .mono { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }

    .top-nav {
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      background: var(--bg-nav);
      border-bottom: 1px solid var(--border-subtle);
      position: sticky;
      top: 0;
      z-index: 50;
    }

    .nav-pill-group {
      background: var(--bg-pill);
      border: 1px solid var(--border-subtle);
    }

    .view-tab-btn {
      transition: all 0.18s ease;
      font-weight: 500;
      cursor: pointer;
      color: var(--text-secondary);
    }
    .view-tab-btn.active {
      background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
      color: #ffffff;
      box-shadow: 0 2px 10px rgba(14, 165, 233, 0.28);
    }

    .theme-toggle-btn {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      cursor: pointer;
      transition: all 0.18s ease;
    }
    .theme-toggle-btn:hover {
      border-color: var(--border-strong);
      color: var(--text-primary);
    }

    .surface-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      box-shadow: var(--card-shadow);
    }

    .run-row:hover { background: var(--bg-hover); }

    .product-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      box-shadow: var(--card-shadow);
      transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
    }
    .product-card:hover {
      transform: translateY(-3px);
      border-color: var(--border-strong);
      box-shadow: 0 12px 24px -6px rgba(14, 165, 233, 0.16);
    }
    .product-img { background: #ffffff; }
    .product-img img { transition: transform 0.3s ease; }
    .product-card:hover .product-img img { transform: scale(1.04); }
    @media (max-width: 640px) {
      .product-card { border-radius: 10px; }
      .product-img { height: 150px; }
      .product-info { padding: 10px; }
      .product-name { font-size: 11px; line-height: 1.35; }
      .product-price { font-size: 13px; }
      .product-original { font-size: 10px; }
      .discount-badge { font-size: 9px; padding: 2px 5px; }
    }
    @media (min-width: 641px) {
      .product-card { border-radius: 12px; }
      .product-img { height: 205px; }
      .product-info { padding: 12px; }
      .product-name { font-size: 13px; }
      .product-price { font-size: 15px; }
      .product-original { font-size: 12px; }
    }
    @media (min-width: 1024px) {
      .product-img { height: 240px; }
      .product-info { padding: 14px; }
    }

    .discount-badge {
      background: rgba(15, 23, 42, 0.82);
      backdrop-filter: blur(4px);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.35);
    }
    .product-name { color: var(--text-primary); }
    .product-price { color: var(--accent-price); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .product-original { color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }

    .search-box {
      background: var(--bg-elevated);
      border: 1px solid var(--border-subtle);
      color: var(--text-primary);
    }
    .search-box:focus {
      border-color: var(--accent-cyan);
      outline: none;
      box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.18);
    }

    .pill-btn {
      background: var(--bg-surface);
      color: var(--text-secondary);
      border: 1px solid var(--border-subtle);
      white-space: nowrap;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .pill-btn:hover:not(.active) {
      border-color: var(--border-strong);
      color: var(--text-primary);
    }
    .pill-btn.active {
      background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
      color: #ffffff;
      border-color: transparent;
      box-shadow: 0 2px 10px rgba(14, 165, 233, 0.25);
    }

    .btn-primary {
      background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
      color: #ffffff;
      box-shadow: 0 2px 10px rgba(14, 165, 233, 0.25);
      transition: opacity 0.15s ease;
    }
    .btn-primary:hover { opacity: 0.92; }

    .filter-pill { color: var(--text-secondary); cursor: pointer; }
    .filter-pill.active {
      background: var(--bg-elevated);
      color: var(--text-primary);
      box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }

    .cli-drawer {
      background: #090d16;
      color: #e2e8f0;
      color-scheme: only dark;
    }

    #back-to-top {
      position: fixed;
      bottom: 20px;
      right: 20px;
      width: 40px;
      height: 40px;
      background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
      color: white;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 6px 16px rgba(14, 165, 233, 0.35);
      cursor: pointer;
      opacity: 0;
      visibility: hidden;
      transition: all 0.25s ease;
      z-index: 40;
    }
    #back-to-top.show { opacity: 1; visibility: visible; }
    #back-to-top svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2.5; }
    .scrollbar-hide::-webkit-scrollbar { display: none; }
    .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
    .logs-scroll {
      max-height: 920px;
      overflow-y: auto;
      overscroll-behavior: contain;
      scrollbar-gutter: stable;
      scrollbar-width: thin;
      scrollbar-color: rgba(148, 163, 184, 0.35) transparent;
    }
    .logs-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
    .logs-scroll::-webkit-scrollbar-track { background: transparent; margin: 4px 0; }
    .logs-scroll::-webkit-scrollbar-thumb {
      background: rgba(148, 163, 184, 0.32);
      border-radius: 9999px;
      border: 1px solid transparent;
      background-clip: content-box;
    }
    .logs-scroll::-webkit-scrollbar-thumb:hover {
      background: rgba(56, 189, 248, 0.65);
    }
    html.light .logs-scroll {
      scrollbar-color: rgba(100, 116, 139, 0.38) transparent;
    }
    html.light .logs-scroll::-webkit-scrollbar-thumb {
      background: rgba(100, 116, 139, 0.28);
    }
    html.light .logs-scroll::-webkit-scrollbar-thumb:hover {
      background: rgba(2, 132, 199, 0.6);
    }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- 极简顶栏 -->
  <header class="top-nav">
    <div class="max-w-7xl mx-auto px-3 sm:px-6 py-2.5 flex items-center justify-between gap-2">
      <div class="flex items-center gap-2.5">
        <span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
        <h1 class="text-sm sm:text-base font-bold tracking-tight">Discount Hub</h1>
      </div>

      <div class="flex items-center gap-2 sm:gap-3">
        <span id="header-clock" class="hidden sm:inline text-xs mono" style="color: var(--text-secondary);">--:--:--</span>

        <nav class="nav-pill-group flex items-center p-0.5 rounded-xl" aria-label="View Switcher">
          <button id="nav-btn-products" class="view-tab-btn active px-3 py-1 rounded-lg text-xs sm:text-sm" onclick="switchMainView('products')">Products</button>
          <button id="nav-btn-logs" class="view-tab-btn px-3 py-1 rounded-lg text-xs sm:text-sm" onclick="switchMainView('logs')">Logs</button>
        </nav>

        <button id="theme-toggle" type="button" class="theme-toggle-btn px-2.5 py-1 sm:py-1.5 rounded-xl text-xs font-medium flex items-center gap-1.5" onclick="toggleColorScheme()" aria-label="Toggle color scheme">
          <span id="theme-toggle-icon" aria-hidden="true">🌙</span>
          <span id="theme-toggle-label" class="hidden sm:inline">Dark</span>
        </button>
      </div>
    </div>
  </header>

  <main class="flex-1 max-w-7xl w-full mx-auto px-3 py-4 sm:p-5 lg:p-6">

    <!-- ========================= View 1: Products ========================= -->
    <div id="view-products" class="block">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-2.5 sm:gap-3 mb-4 sm:mb-5">
        <div class="flex flex-wrap items-center gap-1.5 sm:gap-2">
          <div class="flex gap-1.5" role="group" aria-label="Category">
            <button class="pill-btn type-btn active px-3 py-1.5 rounded-lg text-xs sm:text-sm font-medium" data-type="men" aria-pressed="true">Men</button>
            <button class="pill-btn type-btn px-3 py-1.5 rounded-lg text-xs sm:text-sm font-medium" data-type="women" aria-pressed="false">Women</button>
          </div>

          <span class="hidden sm:inline-block h-4 w-px mx-1" style="background: var(--border-subtle);"></span>

          <div class="flex gap-1.5 overflow-x-auto scrollbar-hide" role="group" aria-label="Discount">
            <button class="pill-btn tab-btn active px-2.5 sm:px-3 py-1.5 rounded-lg text-xs sm:text-sm font-medium" data-discount="70% off">70% Off</button>
            <button class="pill-btn tab-btn px-2.5 sm:px-3 py-1.5 rounded-lg text-xs sm:text-sm font-medium" data-discount="65% off">65% Off</button>
            <button class="pill-btn tab-btn px-2.5 sm:px-3 py-1.5 rounded-lg text-xs sm:text-sm font-medium" data-discount="60% off">60% Off</button>
            <button class="pill-btn tab-btn px-2.5 sm:px-3 py-1.5 rounded-lg text-xs sm:text-sm font-medium" data-discount="all">All</button>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <span class="text-xs mono px-1.5 py-1 hidden sm:inline" style="color: var(--text-secondary);" id="update-time"></span>
          <span class="text-xs mono font-semibold px-2.5 py-1 rounded-lg" style="background: var(--bg-pill); color: var(--accent-cyan);" id="item-count">0 items</span>
          <select id="brand-filter" aria-label="Filter by brand" class="search-box px-2.5 py-1.5 text-xs sm:text-sm rounded-lg max-w-[130px] sm:max-w-[140px]">
            <option value="all">All Brands</option>
          </select>
          <input type="search" id="search" aria-label="Search products" placeholder="Search products..." class="search-box flex-1 sm:flex-none sm:w-44 min-w-[120px] px-3 py-1.5 text-xs sm:text-sm rounded-lg">
        </div>
      </div>

      <div id="product-grid" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2.5 sm:gap-4"></div>
    </div>

    <!-- ========================= View 2: Logs (Style B Outer + Style C Drawer) ========================= -->
    <div id="view-logs" class="hidden max-w-5xl mx-auto space-y-3.5">

      <!-- 项目 Tab + 过滤器 + Run Now -->
      <div class="flex flex-wrap items-center justify-between gap-2.5">
        <div class="flex flex-wrap items-center gap-1.5" id="project-tabs">
          <button class="pill-btn project-tab-btn active px-3 py-1.5 rounded-xl text-xs font-medium" data-project="end70" onclick="switchProject('end70')">
            End70New <span class="mono opacity-80 ml-1" id="badge-end70">--</span>
          </button>
          <button class="pill-btn project-tab-btn px-3 py-1.5 rounded-xl text-xs font-medium" data-project="end" onclick="switchProject('end')">
            End Monitor <span class="mono opacity-80 ml-1" id="badge-end">--</span>
          </button>
          <button class="pill-btn project-tab-btn px-3 py-1.5 rounded-xl text-xs font-medium" data-project="thelasthunt" onclick="switchProject('thelasthunt')">
            TheLastHunt <span class="mono opacity-80 ml-1" id="badge-thelasthunt">--</span>
          </button>
        </div>

        <div class="flex items-center gap-2 ml-auto">
          <div class="nav-pill-group flex p-0.5 rounded-lg text-xs">
            <button onclick="setRunFilter('all')" id="flt-all" class="filter-pill active px-2.5 py-1 rounded-md font-medium">All</button>
            <button onclick="setRunFilter('push')" id="flt-push" class="filter-pill px-2.5 py-1 rounded-md font-medium">Pushed</button>
            <button onclick="setRunFilter('err')" id="flt-err" class="filter-pill px-2.5 py-1 rounded-md font-medium">Errors</button>
          </div>
          <button onclick="triggerCurrentProjectScrape()" class="btn-primary px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer shrink-0">
            Run Now
          </button>
        </div>
      </div>

      <!-- 单行状态摘要条 -->
      <div class="surface-card rounded-xl px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div class="flex items-center gap-2.5 min-w-0">
          <span id="strip-health" class="inline-flex items-center gap-1.5 font-semibold text-emerald-500 shrink-0">
            <i class="w-2 h-2 rounded-full bg-emerald-500 inline-block"></i> Operational
          </span>
          <span style="color: var(--border-strong);">|</span>
          <span id="strip-detail" class="mono truncate" style="color: var(--text-primary);">--</span>
        </div>

        <div class="flex items-center gap-3 mono shrink-0" style="color: var(--text-secondary);">
          <span id="trigger-msg" style="color: var(--accent-cyan);"></span>
          <span id="strip-schedule">--</span>
          <span id="strip-avg">Avg --</span>
        </div>
      </div>

      <!-- 结构化运行流水表 -->
      <div class="surface-card rounded-2xl overflow-hidden">
        <div class="px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 text-xs" style="border-bottom: 1px solid var(--border-subtle); color: var(--text-secondary);">
          <span>Runs <span class="hidden sm:inline">(Click row to expand rich details &amp; alert payload)</span></span>
          <div class="flex flex-wrap items-center gap-2.5 sm:gap-3">
            <div class="inline-flex items-center gap-1" aria-label="Page Size">
              <span class="hidden sm:inline mr-0.5" style="color: var(--text-muted);">Per page</span>
              <button onclick="setPageSize(20)" id="ps-20" class="filter-pill px-2 py-0.5 rounded-md border text-[11px] font-medium cursor-pointer transition active">20</button>
              <button onclick="setPageSize(50)" id="ps-50" class="filter-pill px-2 py-0.5 rounded-md border text-[11px] font-medium cursor-pointer transition">50</button>
              <button onclick="setPageSize(100)" id="ps-100" class="filter-pill px-2 py-0.5 rounded-md border text-[11px] font-medium cursor-pointer transition">100</button>
            </div>
            <label class="inline-flex items-center gap-1.5 cursor-pointer select-none">
              <input type="checkbox" id="auto-refresh-chk" checked onchange="toggleAutoRefresh()" class="rounded">
              <span>Auto (5s)</span>
            </label>
            <span class="mono" id="row-count">0 runs</span>
          </div>
        </div>

        <div id="runs-list" class="logs-scroll divide-y" style="border-color: var(--border-subtle);">
          <div class="px-4 py-8 text-center text-xs" style="color: var(--text-muted);">Loading execution history...</div>
        </div>

        <div id="runs-pagination" class="px-4 py-2.5 flex flex-wrap items-center justify-between gap-2 text-xs" style="border-top: 1px solid var(--border-subtle); color: var(--text-secondary);">
          <span id="page-summary" class="mono">0–0 / 0</span>
          <div id="page-controls" class="flex items-center gap-1"></div>
        </div>
      </div>

    </div>
  </main>

  <button id="back-to-top" title="Back to top" aria-label="Back to top">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 15l-6-6-6 6" /></svg>
  </button>

  <script src="data.js"></script>

  <script>
    const schemeMedia = window.matchMedia('(prefers-color-scheme: dark)');
    function getSystemScheme() { return schemeMedia.matches ? 'dark' : 'light'; }
    function getStoredScheme() {
      try {
        const val = localStorage.getItem('color-scheme');
        return (val === 'dark' || val === 'light') ? val : null;
      } catch (e) { return null; }
    }
    function getRenderedScheme() { return getStoredScheme() || getSystemScheme(); }
    function syncThemeUI() {
      const meta = document.querySelector('meta[name="color-scheme"]');
      const stored = getStoredScheme();
      if (meta) meta.content = stored ?? 'light dark';
      const rendered = getRenderedScheme();
      const iconEl = document.getElementById('theme-toggle-icon');
      const labelEl = document.getElementById('theme-toggle-label');
      if (iconEl && labelEl) {
        iconEl.textContent = rendered === 'dark' ? '☀️' : '🌙';
        labelEl.textContent = rendered === 'dark' ? 'Light' : 'Dark';
      }
    }
    function toggleColorScheme() {
      const rendered = getRenderedScheme();
      const target = rendered === 'dark' ? 'light' : 'dark';
      const sys = getSystemScheme();
      try {
        if (target !== sys) localStorage.setItem('color-scheme', target);
        else localStorage.removeItem('color-scheme');
      } catch (e) {}
      syncThemeUI();
    }
    schemeMedia.addEventListener('change', syncThemeUI);
    window.addEventListener('storage', (e) => { if (e.key === 'color-scheme' || e.key === null) syncThemeUI(); });
    syncThemeUI();

    let currentMainView = 'products';
    let currentProject = 'end70';
    let currentFilter = 'all';
    const VALID_PAGE_SIZES = [20, 50, 100];
    let pageSize = 20;
    try {
      const savedSize = parseInt(localStorage.getItem('dh_logs_page_size') || '20', 10);
      if (VALID_PAGE_SIZES.includes(savedSize)) pageSize = savedSize;
    } catch (e) {}
    let currentPage = 1;
    let lastRenderedProject = '';
    let latestPayload = null;
    let lastRunsSignature = '';
    let logPollTimer = null;
    const expandedRunIds = new Set();
    const runModeMap = {};

    function syncPageSizePills() {
      VALID_PAGE_SIZES.forEach(s => {
        const btn = document.getElementById('ps-' + s);
        if (btn) btn.classList.toggle('active', s === pageSize);
      });
    }
    syncPageSizePills();

    function setPageSize(size) {
      if (!VALID_PAGE_SIZES.includes(size) || size === pageSize) return;
      pageSize = size;
      currentPage = 1;
      try { localStorage.setItem('dh_logs_page_size', String(size)); } catch (e) {}
      syncPageSizePills();
      if (latestPayload) renderRuns(latestPayload, true);
    }

    function setPage(page) {
      currentPage = Math.max(1, page);
      if (latestPayload) renderRuns(latestPayload, true);
    }

    function switchMainView(view) {
      currentMainView = view;
      document.getElementById('view-products').classList.toggle('hidden', view !== 'products');
      document.getElementById('view-logs').classList.toggle('hidden', view !== 'logs');
      document.getElementById('nav-btn-products').classList.toggle('active', view === 'products');
      document.getElementById('nav-btn-logs').classList.toggle('active', view === 'logs');
      window.location.hash = view;
      if (view === 'logs') fetchLogStatus();
    }

    function switchProject(projKey) {
      currentProject = projKey;
      currentPage = 1;
      document.querySelectorAll('#project-tabs .project-tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.project === projKey);
      });
      fetchLogStatus();
    }

    function setRunFilter(filterVal) {
      currentFilter = filterVal;
      currentPage = 1;
      ['all', 'push', 'err'].forEach(f => {
        const btn = document.getElementById('flt-' + f);
        if (btn) btn.classList.toggle('active', f === filterVal);
      });
      if (latestPayload) renderRuns(latestPayload, true);
    }

    function escapeHtml(str) {
      if (str === null || str === undefined) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    window.__serverTimeOffsetMs = 0;
    function formatShanghaiTime(ms) {
      const parts = new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      }).formatToParts(new Date(ms));
      const map = {};
      parts.forEach(p => { map[p.type] = p.value; });
      return `${map.hour}:${map.minute}:${map.second}`;
    }
    setInterval(() => {
      const el = document.getElementById('header-clock');
      if (el) el.textContent = formatShanghaiTime(Date.now() + (window.__serverTimeOffsetMs || 0));
    }, 1000);

    function toggleRunExpand(runId) {
      if (expandedRunIds.has(runId)) expandedRunIds.delete(runId);
      else expandedRunIds.add(runId);
      if (latestPayload) renderRuns(latestPayload);
    }

    function toggleRunMode(runId, event) {
      event.stopPropagation();
      runModeMap[runId] = runModeMap[runId] === 'raw' ? 'rich' : 'raw';
      if (latestPayload) renderRuns(latestPayload);
    }

    let cachedRunRowHeight = 0;
    function syncLogsScrollMaxHeight() {
      const listEl = document.getElementById('runs-list');
      if (!listEl) return;
      const firstRow = listEl.querySelector('.run-row');
      if (firstRow) {
        const h = firstRow.getBoundingClientRect().height;
        if (h > 0) cachedRunRowHeight = h;
      }
      if (cachedRunRowHeight > 0) {
        // 以 20 条未展开日志的总高度为滚动容器基准上限（含 19 条 1px divide-y 边线）
        listEl.style.maxHeight = `${Math.ceil(cachedRunRowHeight * 20 + 20)}px`;
      }
    }
    window.addEventListener('resize', syncLogsScrollMaxHeight);

    function renderPagination(totalItems, totalPages, startIdx, endIdx) {
      const summaryEl = document.getElementById('page-summary');
      const controlsEl = document.getElementById('page-controls');
      if (!summaryEl || !controlsEl) return;

      if (totalItems === 0) {
        summaryEl.textContent = '0–0 / 0';
        controlsEl.innerHTML = '';
        return;
      }

      summaryEl.textContent = `${startIdx + 1}–${endIdx} / ${totalItems} · Page ${currentPage}/${totalPages}`;
      const buttons = [];
      const prevDisabled = currentPage <= 1;
      buttons.push(
        `<button onclick="setPage(${currentPage - 1})" ${prevDisabled ? 'disabled' : ''} class="filter-pill px-2.5 py-1 rounded-lg border text-xs font-medium transition ${prevDisabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}">Prev</button>`
      );
      for (let p = 1; p <= totalPages; p++) {
        buttons.push(
          `<button onclick="setPage(${p})" class="filter-pill px-2.5 py-1 rounded-lg border text-xs font-medium cursor-pointer transition ${p === currentPage ? 'active' : ''}">${p}</button>`
        );
      }
      const nextDisabled = currentPage >= totalPages;
      buttons.push(
        `<button onclick="setPage(${currentPage + 1})" ${nextDisabled ? 'disabled' : ''} class="filter-pill px-2.5 py-1 rounded-lg border text-xs font-medium transition ${nextDisabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}">Next</button>`
      );
      controlsEl.innerHTML = buttons.join('');
    }

    function renderRuns(data, resetScroll = false) {
      const counts = data.project_counts || {};
      ['end70', 'end', 'thelasthunt'].forEach(k => {
        const badgeEl = document.getElementById('badge-' + k);
        if (badgeEl && counts[k] !== undefined) badgeEl.textContent = counts[k];
      });

      const healthEl = document.getElementById('strip-health');
      if (data.healthy) {
        healthEl.className = 'inline-flex items-center gap-1.5 font-semibold text-emerald-500 shrink-0';
        healthEl.innerHTML = '<i class="w-2 h-2 rounded-full bg-emerald-500 inline-block"></i> Operational';
      } else {
        healthEl.className = 'inline-flex items-center gap-1.5 font-semibold text-rose-500 shrink-0';
        healthEl.innerHTML = '<i class="w-2 h-2 rounded-full bg-rose-500 inline-block"></i> Error Detected';
      }

      document.getElementById('strip-detail').textContent = data.detail_stats || '--';
      document.getElementById('strip-schedule').textContent = data.cron_schedule || '--';
      document.getElementById('strip-avg').textContent = 'Avg ' + (data.avg_duration || '--');

      const cappedRuns = (data.runs || []).slice(0, 100);
      const filtered = cappedRuns.filter(r => currentFilter === 'all' || r.status === currentFilter);
      document.getElementById('row-count').textContent = `${filtered.length} runs`;

      const listEl = document.getElementById('runs-list');
      if (filtered.length === 0) {
        listEl.innerHTML = `<div class="px-4 py-8 text-center text-xs" style="color: var(--text-muted);">No matching runs</div>`;
        renderPagination(0, 1, 0, 0);
        return;
      }

      const prevScrollTop = listEl.scrollTop;
      const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;
      const startIdx = (currentPage - 1) * pageSize;
      const endIdx = Math.min(filtered.length, startIdx + pageSize);
      const pageRuns = filtered.slice(startIdx, endIdx);

      listEl.innerHTML = pageRuns.map(r => {
        const isExpanded = expandedRunIds.has(r.id);
        const mode = runModeMap[r.id] || 'rich';
        const dotCls = r.status === 'ok' ? 'bg-emerald-500' : (r.status === 'push' ? 'bg-sky-500' : 'bg-rose-500');
        const tagHtml = r.status === 'ok'
          ? `<span class="px-2 py-0.5 rounded-md text-xs font-medium" style="background: var(--bg-pill); color: var(--text-secondary);">${escapeHtml(r.tag)}</span>`
          : (r.status === 'push'
            ? `<span class="px-2 py-0.5 rounded-md text-xs font-medium bg-sky-500/15 text-sky-400">${escapeHtml(r.tag)}</span>`
            : `<span class="px-2 py-0.5 rounded-md text-xs font-medium bg-rose-500/15 text-rose-400">${escapeHtml(r.tag)}</span>`);

        const cliTag = r.status === 'ok'
          ? '<span class="text-emerald-400 font-semibold">[OK]</span>'
          : (r.status === 'push' ? '<span class="text-sky-400 font-semibold">[PUSH]</span>' : '<span class="text-rose-400 font-semibold">[ERR]</span>');

        let drawerInner = '';
        if (isExpanded) {
          if (mode === 'raw') {
            drawerInner = `<pre class="text-zinc-300 whitespace-pre-wrap break-words leading-6 overflow-x-auto">${escapeHtml(r.raw)}</pre>`;
          } else {
            const fieldsHtml = (r.fields || []).map(([k, v]) => `
              <div class="flex flex-col sm:flex-row sm:gap-3 py-1.5 border-b border-zinc-800/70 last:border-0">
                <span class="text-zinc-400 sm:w-36 shrink-0">${escapeHtml(k)}:</span>
                <span class="text-zinc-100 whitespace-pre-wrap break-words leading-relaxed">${escapeHtml(v)}</span>
              </div>
            `).join('');

            const pushHtml = r.push_preview ? `
              <div class="mt-3 pt-3 border-t border-zinc-800">
                <div class="text-sky-400 font-semibold mb-1.5">▾ DingTalk Payload Preview:</div>
                <div class="bg-zinc-900/90 border border-zinc-800 rounded-xl p-3 text-zinc-200 whitespace-pre-wrap break-words leading-6">${escapeHtml(r.push_preview)}</div>
              </div>
            ` : '';

            drawerInner = `
              <div class="mb-2.5 flex flex-wrap items-center gap-2">
                <span class="text-zinc-500">${escapeHtml(r.full_time || r.time)}</span>
                ${cliTag}
                <span class="text-zinc-100">${escapeHtml(r.summary)}</span>
                <span class="text-zinc-500">(${escapeHtml(r.dur)})</span>
              </div>
              <div class="bg-zinc-900/50 border border-zinc-800/90 rounded-xl px-3.5 py-1.5">
                ${fieldsHtml}
              </div>
              ${pushHtml}
            `;
          }
        }

        return `
          <div style="border-color: var(--border-subtle);">
            <button onclick="toggleRunExpand('${escapeHtml(r.id)}')" class="run-row w-full px-3.5 sm:px-4 py-3 flex items-center justify-between gap-2.5 text-left transition cursor-pointer">
              <div class="flex items-center gap-2.5 sm:gap-3 min-w-0">
                <span class="w-2 h-2 rounded-full shrink-0 ${dotCls}"></span>
                <span class="mono text-xs shrink-0" style="color: var(--text-secondary);">${escapeHtml(r.time)}</span>
                <span class="text-xs font-medium truncate">${escapeHtml(r.summary)}</span>
              </div>
              <div class="flex items-center gap-2 sm:gap-3 shrink-0">
                ${tagHtml}
                <span class="mono text-xs w-12 text-right hidden xs:inline sm:inline" style="color: var(--text-secondary);">${escapeHtml(r.dur)}</span>
                <span class="text-xs transition-transform ${isExpanded ? 'rotate-180' : ''}" style="color: var(--text-muted);">▾</span>
              </div>
            </button>

            ${isExpanded ? `
              <div class="cli-drawer border-t" style="border-color: var(--border-subtle);">
                <div class="px-4 py-2 bg-zinc-900/90 border-b border-zinc-800 flex items-center justify-between text-xs mono text-zinc-400">
                  <span class="truncate">${escapeHtml(data.log_file || '')} · ${escapeHtml(r.full_time || r.time)}</span>
                  <button onclick="toggleRunMode('${escapeHtml(r.id)}', event)" class="px-2.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-200 cursor-pointer shrink-0 ml-2">
                    ${mode === 'raw' ? 'Rich View' : 'Raw Log'}
                  </button>
                </div>
                <div class="p-3.5 sm:p-4 text-xs mono">
                  ${drawerInner}
                </div>
              </div>
            ` : ''}
          </div>
        `;
      }).join('');
      syncLogsScrollMaxHeight();
      renderPagination(filtered.length, totalPages, startIdx, endIdx);
      listEl.scrollTop = resetScroll ? 0 : prevScrollTop;
    }

    async function fetchLogStatus(forceRender = false) {
      try {
        const base = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
        const res = await fetch(base + `api/status?project=${currentProject}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        latestPayload = data;
        if (data.server_ts_ms) {
          window.__serverTimeOffsetMs = data.server_ts_ms - Date.now();
        }
        const projectChanged = data.project !== lastRenderedProject;
        const sig = JSON.stringify({
          project: data.project,
          filter: currentFilter,
          counts: data.project_counts,
          runs: (data.runs || []).map(r => [r.id, r.tag, r.dur])
        });
        if (forceRender || sig !== lastRunsSignature) {
          lastRunsSignature = sig;
          lastRenderedProject = data.project;
          renderRuns(data, projectChanged);
        }
      } catch (err) {
        document.getElementById('runs-list').innerHTML = `<div class="px-4 py-6 text-center text-xs text-rose-500">Failed to load logs: ${escapeHtml(err.message || err)}</div>`;
      }
    }

    async function triggerCurrentProjectScrape() {
      const msgEl = document.getElementById('trigger-msg');
      msgEl.textContent = 'Running...';
      try {
        const base = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
        const res = await fetch(base + `api/trigger?project=${currentProject}`, { method: 'POST' });
        await res.json();
        setTimeout(fetchLogStatus, 1400);
        setTimeout(fetchLogStatus, 3400);
      } catch (err) {
        msgEl.textContent = 'Trigger failed';
      }
      setTimeout(() => { msgEl.textContent = ''; }, 4500);
    }

    function toggleAutoRefresh() {
      const enabled = document.getElementById('auto-refresh-chk').checked;
      if (logPollTimer) clearInterval(logPollTimer);
      if (enabled) {
        logPollTimer = setInterval(() => {
          if (currentMainView === 'logs') fetchLogStatus();
        }, 5000);
      }
    }

    // Products Logic
    const grid = document.getElementById('product-grid');
    const searchInput = document.getElementById('search');
    const countLabel = document.getElementById('item-count');
    const brandFilter = document.getElementById('brand-filter');
    const tabBtns = document.querySelectorAll('.tab-btn');
    const typeBtns = document.querySelectorAll('.type-btn');

    let currentType = 'men';
    let currentDiscount = '70% off';
    let currentSearch = '';
    let currentBrand = 'all';

    function extractBrand(name) {
      if (!name) return 'Unknown';
      return name.trim().split(' ')[0];
    }

    function filterAndRender() {
      if (!window.products) return;
      let filtered = getTypeProducts();
      if (currentDiscount !== 'all') filtered = filtered.filter(p => p.discount === currentDiscount);
      if (currentBrand !== 'all') filtered = filtered.filter(p => extractBrand(p.name).toLowerCase() === currentBrand.toLowerCase());
      if (currentSearch) filtered = filtered.filter(p => p.name.toLowerCase().includes(currentSearch.toLowerCase()));
      renderProducts(filtered);
    }

    function renderProducts(items) {
      grid.innerHTML = '';
      countLabel.textContent = `${items.length} items`;
      if (items.length === 0) {
        grid.innerHTML = '<div class="col-span-full text-center py-12 text-sm" style="color: var(--text-muted);">No matching products found</div>';
        return;
      }
      items.forEach(product => {
        const card = document.createElement('div');
        card.className = 'product-card overflow-hidden flex flex-col';
        const imgUrl = product.image_url || 'https://via.placeholder.com/300x400?text=No+Image';
        const formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency: product.currency || 'USD', maximumFractionDigits: 2 });
        const price = product.discounted_price == null ? 'N/A' : formatter.format(product.discounted_price);
        const originalPrice = product.original_price == null ? 'N/A' : formatter.format(product.original_price);
        card.innerHTML = `
          <a href="${product.url}" target="_blank" rel="noopener noreferrer" class="block relative product-img overflow-hidden">
            <img src="${imgUrl}" alt="${escapeHtml(product.name)}" class="w-full h-full object-cover object-center" loading="lazy">
            <div class="discount-badge absolute top-2 right-2 font-mono font-semibold rounded-md px-2 py-0.5 text-xs">
              -${escapeHtml((product.discount || 'SALE').replace(' off', ''))}
            </div>
          </a>
          <div class="product-info flex-1 flex flex-col">
            <h3 class="product-name font-medium line-clamp-2 mb-2" title="${escapeHtml(product.name)}">${escapeHtml(product.name)}</h3>
            <div class="mt-auto flex items-baseline gap-2">
              <span class="product-price font-bold">${price}</span>
              <span class="product-original line-through">${originalPrice}</span>
            </div>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function getTypeProducts() {
      return (window.products || []).filter(p => (p.type || 'men') === currentType);
    }

    function updateBrands() {
      const brandsMap = new Map();
      getTypeProducts().forEach(p => {
        const brand = extractBrand(p.name);
        const lower = brand.toLowerCase();
        if (!brandsMap.has(lower)) brandsMap.set(lower, brand);
      });
      const sortedBrands = Array.from(brandsMap.values()).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
      brandFilter.innerHTML = '<option value="all">All Brands</option>';
      sortedBrands.forEach(brand => {
        const option = document.createElement('option');
        option.value = brand;
        option.textContent = brand;
        brandFilter.appendChild(option);
      });
      currentBrand = brandsMap.get(currentBrand.toLowerCase()) || 'all';
      brandFilter.value = currentBrand;
    }

    if (window.products) {
      updateBrands();
      filterAndRender();
      if (window.updateTime) document.getElementById('update-time').textContent = `Updated ${window.updateTime}`;
      typeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          currentType = btn.dataset.type;
          typeBtns.forEach(b => {
            const active = b === btn;
            b.classList.toggle('active', active);
            b.setAttribute('aria-pressed', String(active));
          });
          updateBrands();
          filterAndRender();
        });
      });
      tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          tabBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          currentDiscount = btn.dataset.discount;
          filterAndRender();
        });
      });
      brandFilter.addEventListener('change', (e) => { currentBrand = e.target.value; filterAndRender(); });
      searchInput.addEventListener('input', (e) => { currentSearch = e.target.value; filterAndRender(); });
    } else {
      grid.innerHTML = '<div class="col-span-full text-center py-12 text-sm" style="color: var(--text-muted);">No product data available</div>';
    }

    const backToTopBtn = document.getElementById('back-to-top');
    window.addEventListener('scroll', () => backToTopBtn.classList.toggle('show', window.scrollY > 300));
    backToTopBtn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

    if (window.location.hash === '#logs') switchMainView('logs');
    else switchMainView('products');
    toggleAutoRefresh();
  </script>
</body>
</html>
"""


class MultiProjectLogHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_HEAD(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in ("", "/log", "/logs", "/index.html") or parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
        elif path.endswith("/data.js"):
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.end_headers()
        elif path.endswith("/api/status"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/log", "/logs", "/index.html") or parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif path.endswith("/data.js"):
            data_js_path = os.path.join(BASE_DIR, "data.js")
            if os.path.exists(data_js_path):
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                with open(data_js_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.end_headers()
                self.wfile.write("window.products = []; window.updateTime = 'N/A';".encode("utf-8"))

        elif path.endswith("/api/status"):
            query = parse_qs(parsed.query)
            proj = query.get("project", ["end70"])[0]
            data = get_project_status(proj)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.endswith("/api/trigger"):
            query = parse_qs(parsed.query)
            proj = query.get("project", ["end70"])[0]
            ok, msg = trigger_scrape_background(proj)

            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "message": msg}, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def run():
    server = HTTPServer((HOST, PORT), MultiProjectLogHandler)
    print(f"Log & Product Web server running at http://{HOST}:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run()
