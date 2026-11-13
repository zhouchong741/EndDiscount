import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import requests

# END. Clothing (China Market / USD) Algolia 公开只读配置
ALGOLIA_APP_ID = os.getenv("ALGOLIA_APP_ID", "7Q9DS9O2PB")
ALGOLIA_API_KEY = os.getenv("ALGOLIA_API_KEY", "1def7e2b2b12cd087ca4d8dabe187edb")
ALGOLIA_INDEX = os.getenv("ALGOLIA_INDEX", "shopify_products_china_en")
ALGOLIA_ENDPOINT = f"https://{ALGOLIA_APP_ID.lower()}-3.algolianet.com/1/indexes/*/queries"

COLLECTIONS = {
    "men": {"id": "696175427922", "handle": "mens-sale"},
    "women": {"id": "696221991250", "handle": "womens-sale"},
}

CURRENCY = "USD"
TARGET_DISCOUNTS = {"60% off", "65% off", "70% off"}
# 60% off 对应四舍五入下界为 57.5% off，即 price_ratio <= 0.425，放宽到 0.43 防止浮点误差遗漏
MAX_PRICE_RATIO = 0.43
HITS_PER_PAGE = 1000
MAX_RETRIES = 3
RETRY_DELAY = 2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "endclothing_70off.json")
DATA_JS_FILE = os.path.join(BASE_DIR, "data.js")
PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "https://discount.19930216.xyz")

FEISHU_WEBHOOK = os.getenv(
    "FEISHU_WEBHOOK",
    "https://open.feishu.cn/open-apis/bot/v2/hook/e6938e11-3931-4c18-94b1-3658335c5f92",
)


def format_price(value):
    """保持与原脚本一致：整数价格转为 int，小数保留 float。"""
    val = float(value)
    return int(val) if val.is_integer() else round(val, 2)


def compute_discount_label(original_price, discounted_price):
    """
    END. 网站折扣标签规则：按 (1 - 现价/原价) 百分比四舍五入到最近的 5% 档位。
    例如 $19 -> $7 (63.16% off) 标注为 '65% off'。
    """
    if not (0 < discounted_price < original_price):
        raise ValueError(f"Invalid price pair: regular={original_price}, sale={discounted_price}")
    off_pct = (1.0 - (discounted_price / original_price)) * 100.0
    rounded_5 = int(Decimal_round_5(off_pct))
    return f"{rounded_5}% off"


def Decimal_round_5(pct):
    """四舍五入到最近的 5 的倍数。"""
    return int(math.floor((pct / 5.0) + 0.5)) * 5


def normalize_image_url(image_url):
    """
    将 Shopify CDN 原始地址转换为与原版一致的 END. 主站 CDN 地址（附带 width=2100）。
    """
    if not image_url or not isinstance(image_url, str):
        raise ValueError("Missing product image URL")
    if image_url.startswith("//"):
        image_url = "https:" + image_url
    parts = urlsplit(image_url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError(f"Invalid product image URL: {image_url}")
    filename = parts.path.rsplit("/", 1)[-1]
    if not filename:
        raise ValueError(f"Invalid image path: {image_url}")
    query = f"{parts.query}&width=2100" if parts.query else "width=2100"
    return f"https://www.endclothing.com/cdn/shop/files/{filename}?{query}"


def extract_product_from_hit(hit, product_type):
    """从单条 Algolia hit JSON 提取标准商品结构。"""
    handle = hit.get("handle")
    if not isinstance(handle, str) or not re.fullmatch(r"[a-z0-9-]+", handle):
        raise ValueError(f"Missing or invalid product handle: {handle!r}")

    pricing = hit.get("market_pricing_usd") or {}
    raw_orig = pricing.get("compare_at_price")
    raw_disc = pricing.get("price")
    if not raw_orig or not raw_disc or raw_disc >= raw_orig or raw_disc <= 0:
        return None

    original_price = format_price(raw_orig)
    discounted_price = format_price(raw_disc)
    discount = compute_discount_label(original_price, discounted_price)
    if discount not in TARGET_DISCOUNTS:
        return None

    title = (hit.get("title") or "").strip()
    if not title:
        raise ValueError(f"Missing product title for handle={handle}")

    colour = ((hit.get("meta") or {}).get("colour") or {}).get("name")
    colour = colour.strip() if isinstance(colour, str) else ""
    name = f"{title} - {colour}" if colour else title

    raw_image = hit.get("product_image") or hit.get("image")
    image_url = normalize_image_url(raw_image)

    return {
        "type": product_type,
        "name": name,
        "original_price": original_price,
        "discounted_price": discounted_price,
        "currency": CURRENCY,
        "discount": discount,
        "url": f"https://www.endclothing.com/en-cn/products/{handle}",
        "image_url": image_url,
    }


def build_algolia_params(collection_info, page_num):
    filters = (
        f'collection_ids:"{collection_info["id"]}" '
        f"AND inventory_available:true "
        f"AND market_pricing_usd.price_ratio <= {MAX_PRICE_RATIO}"
    )
    return urlencode({
        "hitsPerPage": HITS_PER_PAGE,
        "page": page_num - 1,
        "distinct": "true",
        "filters": filters,
        "ruleContexts": json.dumps([collection_info["handle"], "shopify_default_collection"]),
    })


def query_algolia(session, requests_payload):
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "Content-Type": "application/json",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(
                ALGOLIA_ENDPOINT,
                headers=headers,
                json={"requests": requests_payload},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json()["results"]
        except requests.RequestException as err:
            if attempt == MAX_RETRIES:
                raise
            print(f"Algolia request attempt {attempt}/{MAX_RETRIES} failed: {err}; retrying...", flush=True)
            time.sleep(RETRY_DELAY * attempt)


def scrape_all(session=None):
    own_session = session is None
    if own_session:
        session = requests.Session()
    try:
        # 首次请求：批量拉取 men 和 women 的第 1 页（通常 1 页即可容纳全部 <= 0.43 折扣商品）
        product_types = list(COLLECTIONS.keys())
        initial_payload = [
            {"indexName": ALGOLIA_INDEX, "params": build_algolia_params(COLLECTIONS[pt], 1)}
            for pt in product_types
        ]
        initial_results = query_algolia(session, initial_payload)

        results_by_type = {}
        for pt, first_page in zip(product_types, initial_results):
            nb_hits = first_page.get("nbHits", 0)
            nb_pages = first_page.get("nbPages", 0)
            hits = list(first_page.get("hits", []))

            # 如超过 1000 条则继续拉取剩余分页
            for page_num in range(2, nb_pages + 1):
                more_res = query_algolia(
                    session,
                    [{"indexName": ALGOLIA_INDEX, "params": build_algolia_params(COLLECTIONS[pt], page_num)}],
                )[0]
                hits.extend(more_res.get("hits", []))

            products_dict = {}
            for hit in hits:
                product = extract_product_from_hit(hit, pt)
                if product:
                    products_dict[(pt, product["url"])] = product

            results_by_type[pt] = products_dict

        return results_by_type
    finally:
        if own_session:
            session.close()


def load_existing_data(filepath=OUTPUT_FILE):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {(item.get("type", "men"), item["url"]): item for item in data}
    return {}


def save_data(products_dict, json_path=OUTPUT_FILE, js_path=DATA_JS_FILE):
    products_list = list(products_dict.values())
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(products_list, f, ensure_ascii=False, indent=2)

    json_str = json.dumps(products_list, ensure_ascii=False)
    beijing_tz = timezone(timedelta(hours=8))
    update_time = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M")
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(f"window.products = {json_str};\n")
        f.write(f'window.updateTime = "{update_time}";\n')


def format_change_desc(old_items, new_items):
    diff = len(new_items) - len(old_items)
    if diff > 0:
        return f"+{diff}"
    if diff < 0:
        return f"-{abs(diff)}"
    added = len(set(new_items.keys()) - set(old_items.keys()))
    if added > 0:
        return f"上新 {added} / 下架 {added}"
    if old_items != new_items:
        return "价格或信息更新"
    return "0"


def _feishu_diff_color(desc, diff=None):
    text = str(desc or "").strip()
    if text.startswith("+") or (diff is not None and diff > 0) or "上新" in text:
        return "green"
    if text.startswith("-") or (diff is not None and diff < 0):
        return "red"
    if text == "0":
        return "grey"
    return "green"


def _format_feishu_count_line(prev_count, curr_count, desc):
    diff = (curr_count - prev_count) if prev_count is not None else None
    color = _feishu_diff_color(desc, diff=diff)
    if prev_count is not None:
        return f"{prev_count} -> {curr_count} <font color='{color}'>({desc})</font>"
    return f"{curr_count}（<font color='{color}'>{desc}</font>）"


def build_feishu_card(
    now_str,
    total_count,
    total_desc,
    men_count,
    men_desc,
    women_count,
    women_desc,
    prev_total=None,
    prev_men=None,
    prev_women=None,
):
    """构造飞书 Card JSON 2.0 极简竖向卡片（只调整 UI 样式，不改动推送字段内容）。"""
    total_diff = (total_count - prev_total) if prev_total is not None else 0
    if total_diff > 0:
        header_tpl, tag_color = "green", "green"
    elif total_diff < 0:
        header_tpl, tag_color = "orange", "orange"
    else:
        header_tpl, tag_color = "blue", "blue"

    md_content = "\n".join([
        f"**时间**：{now_str}",
        f"**Men**：{_format_feishu_count_line(prev_men, men_count, men_desc)}",
        f"**Women**：{_format_feishu_count_line(prev_women, women_count, women_desc)}",
        f"**总计**：{_format_feishu_count_line(prev_total, total_count, total_desc)}",
    ])

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "End70 产品数量变化通知"},
            "template": header_tpl,
            "text_tag_list": [
                {
                    "tag": "text_tag",
                    "text": {"tag": "plain_text", "content": "End70"},
                    "color": tag_color,
                }
            ],
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": md_content},
                {"tag": "hr"},
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "点击查看"},
                    "type": "primary",
                    "behaviors": [{"type": "open_url", "default_url": PUBLIC_SITE_URL}],
                },
            ]
        },
    }


def send_feishu_notification(
    total_count,
    total_desc,
    men_count,
    men_desc,
    women_count,
    women_desc,
    webhook=None,
    prev_total=None,
    prev_men=None,
    prev_women=None,
):
    """当数据变化且不在 GitHub Actions 环境中时直接调用飞书 Webhook 推送卡片通知。"""
    webhook_url = webhook or FEISHU_WEBHOOK
    if not webhook_url:
        return False
    beijing_tz = timezone(timedelta(hours=8))
    now_str = datetime.now(beijing_tz).strftime("%Y/%m/%d %H:%M:%S")
    card = build_feishu_card(
        now_str=now_str,
        total_count=total_count,
        total_desc=total_desc,
        men_count=men_count,
        men_desc=men_desc,
        women_count=women_count,
        women_desc=women_desc,
        prev_total=prev_total,
        prev_men=prev_men,
        prev_women=prev_women,
    )
    payload = {
        "msg_type": "interactive",
        "card": card,
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        print(f"Feishu notification sent: status={resp.status_code}, response={resp.text}", flush=True)
        if resp.status_code != 200:
            return False
        try:
            body = json.loads(resp.text) if resp.text else {}
        except Exception:
            return True
        if isinstance(body, dict) and ("code" in body or "StatusCode" in body):
            return body.get("code") == 0 or body.get("StatusCode") == 0
        return True
    except Exception as e:
        print(f"Failed to send Feishu notification: {e}", flush=True)
        return False


def main():
    started = time.perf_counter()
    try:
        existing = load_existing_data()
        initial_count = len(existing)
        existing_by_type = {
            pt: {k: v for k, v in existing.items() if v.get("type", "men") == pt}
            for pt in COLLECTIONS
        }

        results_by_type = scrape_all()
        all_products = {}
        for pt in COLLECTIONS:
            all_products.update(results_by_type[pt])

        final_count = len(all_products)
        final_counts = {pt: len(results_by_type[pt]) for pt in COLLECTIONS}
        prev_men = len(existing_by_type["men"])
        prev_women = len(existing_by_type["women"])
        total_desc = format_change_desc(existing, all_products)
        men_desc = format_change_desc(existing_by_type["men"], results_by_type["men"])
        women_desc = format_change_desc(existing_by_type["women"], results_by_type["women"])
        print(f"Men: {prev_men} -> {final_counts['men']} ({men_desc})", flush=True)
        print(f"Women: {prev_women} -> {final_counts['women']} ({women_desc})", flush=True)
        print(f"Total: {initial_count} -> {final_count} ({total_desc})", flush=True)
        print(f"Link: {PUBLIC_SITE_URL}", flush=True)

        has_changed = (all_products != existing) or not os.path.exists(DATA_JS_FILE)
        if has_changed:
            save_data(all_products)
            # 如果不在 GitHub Actions 环境（如在 VPS 独立运行），直接发送飞书卡片通知
            if os.getenv("GITHUB_ACTIONS") != "true":
                send_feishu_notification(
                    final_count,
                    total_desc,
                    final_counts["men"],
                    men_desc,
                    final_counts["women"],
                    women_desc,
                    prev_total=initial_count,
                    prev_men=prev_men,
                    prev_women=prev_women,
                )
        else:
            print("No change; skipped alert.", flush=True)

        env_file = os.getenv("GITHUB_ENV")
        if env_file:
            with open(env_file, "a", encoding="utf-8") as f:
                f.write(f"DATA_CHANGED={'true' if has_changed else 'false'}\n")
                f.write(f"PRODUCT_COUNT={final_count}\n")
                f.write(f"PRODUCT_CHANGE_DESC={total_desc}\n")
                f.write(f"MEN_PRODUCT_COUNT={final_counts['men']}\n")
                f.write(f"MEN_CHANGE_DESC={men_desc}\n")
                f.write(f"WOMEN_PRODUCT_COUNT={final_counts['women']}\n")
                f.write(f"WOMEN_CHANGE_DESC={women_desc}\n")

        elapsed = time.perf_counter() - started
        print(
            f"Complete: total={final_count} "
            f"(men={final_counts['men']}, women={final_counts['women']}) "
            f"changed={has_changed} elapsed={elapsed:.2f}s",
            flush=True,
        )
        return 0
    except Exception as err:
        print(f"Scrape failed: {type(err).__name__}: {err}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
