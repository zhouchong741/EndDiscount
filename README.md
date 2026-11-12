# END. Clothing 极速版爬虫（纯 Algolia API 直连方案）

本目录为**方案 A（纯 `requests` 直连 Algolia 搜索接口）**的独立实现，完全不影响根目录原有的 GitHub Actions 逻辑。

## 核心改进对比

| 指标 | 原版 (`scrape_endclothing.py`) | 极速版 (`algolia_direct/scrape_fast.py`) |
| :--- | :--- | :--- |
| **运行依赖** | `selenium` + `webdriver-manager` + `Chrome` + `bs4` + `lxml` | **仅需 `requests`** |
| **Cloudflare 拦截风险** | 高（需访问 `www.endclothing.com` 渲染页面） | **无**（直连 `*.algolianet.com` 公开搜索 CDN） |
| **网络请求次数** | ~1,000+ 次（遍历 2 万+ 促销商品，每 20 件请求一次 HTML 卡片） | **1 次批量 POST 请求**（服务端直接按 `price_ratio <= 0.43` 过滤） |
| **单次运行耗时** | 15 ~ 25 分钟（耗时过长时还会因库存变动导致整页跳过） | **~2.5 秒** |
| **数据一致性** | 基准 | 与原版重叠的 492 件商品 **8 个字段 100% 完全一致**，且不会因分页耗时漏抓 |

## 核心原理

1. **服务端预过滤**：Algolia 索引 `shopify_products_china_en` 原生支持按 `market_pricing_usd.price_ratio <= 0.43` 过滤（对应折扣 $\ge 57\%$），将 Men（13,700+ 件）和 Women（7,700+ 件）直接收敛到各 **300~500 件**，单页（`hitsPerPage=1000`）即可一次拉完。
2. **字段直接提取**：Algolia 返回的 `hit` 对象已包含：
   - 标题与颜色：`hit['title']` + `hit['meta']['colour']['name']`
   - 美元原价与现价：`hit['market_pricing_usd']['compare_at_price']` 与 `price`
   - 折扣档位：按 `(1 - price / compare_at_price) * 100` 四舍五入到最近的 `5%`（经全量 513 条历史数据比对，**0 误差**）
   - 图片地址：自动转换为 `https://www.endclothing.com/cdn/shop/files/...&width=2100`

## 运行方式

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 运行单元测试
python3 -m unittest discover -s . -p "test_*.py" -v

# 3. 运行极速抓取（约 2~3 秒完成）
python3 scrape_fast.py
```

运行完成后，直接用浏览器打开 `index.html` 即可查看最新抓取的 **851 件**（Men 473 件 + Women 378 件）60% / 65% / 70% off 促销商品。
