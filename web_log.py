#!/usr/bin/env python3
import json
import os
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8088

# 项目配置定义
PROJECTS_CONFIG = {
    "end70": {
        "id": "end70",
        "name": "End70New (Algolia)",
        "badge": "Algolia 准秒 API",
        "log_file": "/var/log/enddiscount.log",
        "run_script": "/opt/crawler/EndDiscount/run_cron.sh",
        "data_file": os.path.join(BASE_DIR, "endclothing_70off.json"),
        "cron_schedule": "每 5 分钟",
        "default_sub": "60% ~ 70% off 档位",
    },
    "end": {
        "id": "end",
        "name": "End (Node.js)",
        "badge": "Node.js 爬虫",
        "log_file": "/var/log/crawler-end.log",
        "run_script": "/opt/crawler/End/run_cron.sh",
        "data_dir": "/opt/crawler/End/data",
        "cron_schedule": "每 30 分钟",
        "default_sub": "指定分类监控",
    },
    "thelasthunt": {
        "id": "thelasthunt",
        "name": "TheLastHunt (Node.js)",
        "badge": "Node.js 爬虫",
        "log_file": "/var/log/crawler-thelasthunt.log",
        "run_script": "/opt/crawler/TheLastHunt/run_cron.sh",
        "data_dir": "/opt/crawler/TheLastHunt/data",
        "cron_schedule": "每 30 分钟",
        "default_sub": "户外品牌折扣",
    },
}


def get_log_tail(log_path, lines=150):
    if not os.path.exists(log_path):
        return f"日志文件 {log_path} 尚不存在或尚未生成。"
    try:
        res = subprocess.run(
            ["tail", "-n", str(lines), log_path],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return res.stdout or "(日志内容为空)"
    except Exception as e:
        return f"读取日志失败: {e}"


def get_project_status(project_key):
    cfg = PROJECTS_CONFIG.get(project_key, PROJECTS_CONFIG["end70"])
    log_tail = get_log_tail(cfg["log_file"], 150)
    server_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total = 0
    men = 0
    women = 0
    last_mod = "未知"
    detail_stats = ""

    if project_key == "end70":
        data_file = cfg.get("data_file")
        if data_file and os.path.exists(data_file):
            try:
                mtime = os.path.getmtime(data_file)
                last_mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                with open(data_file, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    total = len(items)
                    men = sum(1 for x in items if x.get("type") == "men")
                    women = sum(1 for x in items if x.get("type") == "women")
                detail_stats = f"男装: {men} | 女装: {women}"
            except Exception:
                pass
        else:
            detail_stats = "数据文件就绪中"
    else:
        # Node.js 项目 (End 或 TheLastHunt)
        data_dir = cfg.get("data_dir")
        if data_dir and os.path.exists(data_dir):
            try:
                files = [os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.endswith(".json")]
                if files:
                    latest_file = max(files, key=os.path.getmtime)
                    mtime = os.path.getmtime(latest_file)
                    last_mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                    detail_stats = f"数据文件: {len(files)} 份"
                    total = len(files)
            except Exception:
                pass
        if last_mod == "未知" and os.path.exists(cfg["log_file"]):
            try:
                mtime = os.path.getmtime(cfg["log_file"])
                last_mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        if not detail_stats:
            detail_stats = "独立 Node.js 任务"

    return {
        "project": cfg["id"],
        "name": cfg["name"],
        "badge": cfg["badge"],
        "total": total,
        "detail_stats": detail_stats,
        "data_updated_at": last_mod,
        "cron_schedule": cfg["cron_schedule"],
        "sub_desc": cfg["default_sub"],
        "server_time": server_time,
        "log": log_tail,
    }


def trigger_scrape_background(project_key):
    cfg = PROJECTS_CONFIG.get(project_key)
    if not cfg:
        return False, "未知项目"

    script = cfg["run_script"]
    # 若在本地开发测试，若 /opt 路径不存在则尝试在当前目录找
    if not os.path.exists(script):
        local_script = os.path.join(BASE_DIR, "run_cron.sh")
        if os.path.exists(local_script) and project_key == "end70":
            script = local_script
        else:
            return False, f"执行脚本不存在: {script}"

    def _run():
        try:
            subprocess.run(["/bin/bash", script], timeout=90)
        except Exception as err:
            print(f"Trigger error on {project_key}: {err}", flush=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"{cfg['name']} 抓取已触发"


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>折扣监控情报中心 - END. & TheLastHunt</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root {
      --primary: #ef4444;
      --primary-hover: #dc2626;
      --bg: #f5f5f5;
      --terminal-bg: #030712;
      --terminal-border: #1f2937;
    }
    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg);
      color: #1f2937;
    }
    
    /* 导航栏样式 */
    .top-nav {
      backdrop-filter: blur(12px);
      background: rgba(255, 255, 255, 0.92);
      border-bottom: 1px solid #e5e7eb;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .view-tab-btn {
      transition: all 0.2s ease;
      font-weight: 500;
      cursor: pointer;
    }
    .view-tab-btn.active {
      background-color: #ef4444;
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(239, 68, 68, 0.25);
    }
    .view-tab-btn:not(.active) {
      background-color: #f3f4f6;
      color: #4b5563;
    }
    .view-tab-btn:not(.active):hover {
      background-color: #e5e7eb;
      color: #111827;
    }

    /* 商品卡片与原有样式 */
    .product-card {
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .product-card:hover {
      transform: translateY(-3px);
      box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
    }
    @media (max-width: 640px) {
      .product-card { border-radius: 8px; }
      .product-img { height: 140px; }
      .product-info { padding: 8px; }
      .product-name { font-size: 11px; line-height: 1.3; }
      .product-price { font-size: 13px; }
      .product-original { font-size: 10px; }
      .discount-badge { font-size: 9px; padding: 2px 4px; }
    }
    @media (min-width: 641px) {
      .product-img { height: 200px; }
      .product-info { padding: 12px; }
      .product-name { font-size: 13px; }
      .product-price { font-size: 16px; }
      .product-original { font-size: 12px; }
    }
    @media (min-width: 1024px) {
      .product-img { height: 240px; }
      .product-info { padding: 16px; }
    }
    .search-box {
      background: white;
      border: 1px solid #e5e5e5;
      color: #333;
    }
    .search-box::placeholder { color: #999; }
    .search-box:focus {
      background: white;
      border-color: #ef4444;
      outline: none;
      box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.1);
    }
    .update-time {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 12px;
      display: inline-block;
    }
    #back-to-top {
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 48px;
      height: 48px;
      background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%);
      color: white;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 15px rgba(239, 68, 68, 0.4);
      cursor: pointer;
      opacity: 0;
      visibility: hidden;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      z-index: 1000;
      border: none;
      outline: none;
    }
    #back-to-top.show {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }
    #back-to-top:hover {
      transform: scale(1.1) translateY(-5px);
      box-shadow: 0 6px 20px rgba(239, 68, 68, 0.6);
    }
    #back-to-top:active { transform: scale(0.95); }
    #back-to-top svg {
      width: 24px;
      height: 24px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2.5;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .tab-btn, .type-btn {
      background: #e5e5e5;
      color: #666;
      white-space: nowrap;
      cursor: pointer;
    }
    .tab-btn.active, .type-btn.active {
      background: #ef4444;
      color: white;
    }
    .scrollbar-hide::-webkit-scrollbar { display: none; }
    .scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }

    /* 日志控制中心专属样式 */
    .project-tab-btn {
      padding: 8px 16px;
      font-size: 14px;
      font-weight: 500;
      border-radius: 8px;
      transition: all 0.2s ease;
      cursor: pointer;
    }
    .project-tab-btn.active {
      background-color: #1f2937;
      color: #ffffff;
      box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
    }
    .project-tab-btn:not(.active) {
      background-color: #ffffff;
      color: #6b7280;
      border: 1px solid #e5e7eb;
    }
    .project-tab-btn:not(.active):hover {
      background-color: #f9fafb;
      color: #111827;
    }
    .terminal-box {
      background: var(--terminal-bg);
      border: 1px solid var(--terminal-border);
      border-radius: 10px;
      padding: 16px;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: 13px;
      line-height: 1.55;
      color: #38bdf8;
      height: 520px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
      box-shadow: inset 0 2px 6px rgba(0,0,0,0.4);
    }
    .log-line-started { color: #facc15; font-weight: bold; }
    .log-line-finished { color: #4ade80; }
    .log-line-error { color: #f87171; font-weight: bold; }
    .log-line-info { color: #94a3b8; }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- 顶部主导航栏 -->
  <header class="top-nav shadow-sm">
    <div class="max-w-7xl mx-auto px-3 sm:px-6 py-3 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2">
        <span class="text-xl sm:text-2xl">⚡</span>
        <div>
          <h1 class="text-base sm:text-lg font-bold text-gray-900 leading-tight">Discount Hub</h1>
          <p class="text-[11px] text-gray-500 hidden sm:block">END. & TheLastHunt 抓取监控中心</p>
        </div>
      </div>
      
      <!-- 主视图切换 [折扣商品] 与 [运行日志] -->
      <nav class="flex items-center bg-gray-100 p-1 rounded-xl">
        <button id="nav-btn-products" class="view-tab-btn active px-4 py-1.5 sm:px-5 sm:py-2 rounded-lg text-xs sm:text-sm flex items-center gap-1.5" onclick="switchMainView('products')">
          <span>🛍️</span>
          <span>折扣商品</span>
        </button>
        <button id="nav-btn-logs" class="view-tab-btn px-4 py-1.5 sm:px-5 sm:py-2 rounded-lg text-xs sm:text-sm flex items-center gap-1.5" onclick="switchMainView('logs')">
          <span>📜</span>
          <span>运行日志</span>
        </button>
      </nav>
    </div>
  </header>

  <!-- 主容器 -->
  <main class="flex-1 max-w-7xl w-full mx-auto p-2 sm:p-4 lg:p-6">
    
    <!-- ========================= 视图 1：折扣商品展示 ========================= -->
    <div id="view-products" class="block">
      <!-- 原 End Clothing 头部 -->
      <div class="mb-3 sm:mb-6">
        <div class="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 sm:gap-4">
          <div>
            <h2 class="text-xl sm:text-2xl lg:text-3xl font-bold text-gray-800">End Clothing Sale</h2>
            <p class="text-gray-500 text-xs sm:text-sm mt-1">Discount Collection (60% ~ 70% Off)</p>
          </div>
          <div class="flex flex-wrap items-center gap-2 sm:gap-3">
            <span class="text-xs sm:text-sm text-gray-500 font-medium" id="item-count">Loading...</span>
            <select id="brand-filter" class="search-box px-3 py-1.5 sm:py-2 text-xs sm:text-sm rounded-lg max-w-[120px] sm:max-w-[150px]">
              <option value="all">所有品牌</option>
            </select>
            <input type="text" id="search" placeholder="搜索产品..." class="search-box w-32 sm:w-40 lg:w-48 px-3 py-1.5 sm:py-2 text-xs sm:text-sm rounded-lg">
          </div>
        </div>
      </div>

      <!-- Type Tabs -->
      <div class="flex gap-2 mb-4" role="group" aria-label="商品类型">
        <button class="type-btn active px-5 py-2 rounded-full text-sm font-medium transition-colors" data-type="men" aria-pressed="true">Men</button>
        <button class="type-btn px-5 py-2 rounded-full text-sm font-medium transition-colors" data-type="women" aria-pressed="false">Women</button>
      </div>

      <!-- Discount Tabs -->
      <div class="flex gap-2 mb-4 overflow-x-auto pb-2 scrollbar-hide">
        <button class="tab-btn active px-4 py-2 rounded-full text-sm font-medium transition-colors" data-discount="70% off">70% Off</button>
        <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium transition-colors" data-discount="65% off">65% Off</button>
        <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium transition-colors" data-discount="60% off">60% Off</button>
        <button class="tab-btn px-4 py-2 rounded-full text-sm font-medium transition-colors" data-discount="all">全部</button>
      </div>

      <!-- 更新时间 -->
      <div class="mb-4 sm:mb-6 flex justify-between items-center">
        <span class="update-time" id="update-time">📅 最后更新: 加载中...</span>
      </div>

      <!-- Product Grid: 2列(手机) / 3列(平板) / 4列(桌面) / 5列(大屏) -->
      <div id="product-grid" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2 sm:gap-3 lg:gap-4">
        <!-- Products will be inserted here -->
      </div>

      <!-- Footer -->
      <footer class="mt-8 mb-4 text-center text-gray-400 text-xs">
        <p>Data auto-updates every 5 minutes via RakSmart VPS</p>
      </footer>
    </div>

    <!-- ========================= 视图 2：运行日志中心 ========================= -->
    <div id="view-logs" class="hidden">
      <!-- 爬虫项目切换 Tab -->
      <div class="mb-4 sm:mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 pb-3">
        <div class="flex flex-wrap gap-2" id="project-tabs">
          <button class="project-tab-btn active" data-project="end70" onclick="switchProject('end70')">
            1. End70New (Algolia)
          </button>
          <button class="project-tab-btn" data-project="end" onclick="switchProject('end')">
            2. End (Node.js)
          </button>
          <button class="project-tab-btn" data-project="thelasthunt" onclick="switchProject('thelasthunt')">
            3. TheLastHunt (Node.js)
          </button>
        </div>
        <div class="text-xs text-gray-500 font-mono" id="log-server-time">
          服务器时间: --
        </div>
      </div>

      <!-- 统计卡片网格 -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 mb-4">
        <div class="bg-white border border-gray-200 rounded-xl p-4 shadow-sm">
          <div class="text-xs text-gray-500 mb-1">当前项目</div>
          <div class="text-lg font-bold text-gray-800" id="card-proj-name">End70New</div>
          <div class="text-xs text-emerald-600 mt-1 font-medium" id="card-proj-badge">Algolia 准秒 API</div>
        </div>
        <div class="bg-white border border-gray-200 rounded-xl p-4 shadow-sm">
          <div class="text-xs text-gray-500 mb-1">监控规模 / 详情</div>
          <div class="text-xl font-bold text-gray-800" id="card-proj-total">--</div>
          <div class="text-xs text-gray-500 mt-1" id="card-proj-detail">实时分类结构</div>
        </div>
        <div class="bg-white border border-gray-200 rounded-xl p-4 shadow-sm">
          <div class="text-xs text-gray-500 mb-1">调度策略</div>
          <div class="text-xl font-bold text-blue-600" id="card-proj-schedule">每 5 分钟</div>
          <div class="text-xs text-gray-500 mt-1">Crontab 独立调度锁</div>
        </div>
        <div class="bg-white border border-gray-200 rounded-xl p-4 shadow-sm">
          <div class="text-xs text-gray-500 mb-1">最新数据更新</div>
          <div class="text-sm font-semibold text-gray-800 mt-1" id="card-proj-updated">--</div>
          <div class="text-xs text-emerald-600 mt-1">变动即时推送钉钉群</div>
        </div>
      </div>

      <!-- 控制工具栏 -->
      <div class="flex flex-wrap items-center justify-between gap-3 mb-3 bg-white p-3 rounded-xl border border-gray-200 shadow-sm">
        <div class="flex items-center gap-2 flex-wrap">
          <button class="bg-gray-100 hover:bg-gray-200 text-gray-800 text-xs sm:text-sm font-medium px-3.5 py-1.5 rounded-lg transition" onclick="fetchLogStatus()">
            🔄 刷新日志
          </button>
          <button class="bg-emerald-600 hover:bg-emerald-700 text-white text-xs sm:text-sm font-medium px-3.5 py-1.5 rounded-lg transition flex items-center gap-1 shadow-sm" onclick="triggerCurrentProjectScrape()">
            <span>⚡</span>
            <span>立即抓取一次</span>
          </button>
          <span id="trigger-msg" class="text-xs font-medium text-blue-600 transition-all"></span>
        </div>
        <label class="flex items-center gap-2 text-xs text-gray-600 cursor-pointer select-none">
          <input type="checkbox" id="auto-refresh-chk" checked onchange="toggleAutoRefresh()" class="rounded text-red-500 focus:ring-red-400">
          <span>5 秒自动轮询</span>
        </label>
      </div>

      <!-- 黑色代码终端控制台 -->
      <div class="relative">
        <div class="flex items-center justify-between px-4 py-2 bg-gray-900 text-gray-400 text-xs rounded-t-lg border border-b-0 border-gray-800 font-mono">
          <span id="terminal-header-title">📜 日志输出 (/var/log/enddiscount.log)</span>
          <span class="text-emerald-400">● LIVE</span>
        </div>
        <pre class="terminal-box rounded-t-none" id="log-terminal">正在加载日志...</pre>
      </div>
    </div>
  </main>

  <!-- 回到顶部按钮 -->
  <button id="back-to-top" title="回到顶部">
    <svg viewBox="0 0 24 24">
      <path d="M18 15l-6-6-6 6" />
    </svg>
  </button>

  <!-- 引入商品数据 (由 web_log.py 路由 /data.js 提供) -->
  <script src="data.js"></script>

  <!-- 前端核心控制脚本 -->
  <script>
    // ==========================================
    // 视图切换控制 (Products vs Logs)
    // ==========================================
    let currentMainView = 'products';
    let currentProject = 'end70';
    let logPollTimer = null;

    function switchMainView(view) {
      currentMainView = view;
      const productsDiv = document.getElementById('view-products');
      const logsDiv = document.getElementById('view-logs');
      const btnProducts = document.getElementById('nav-btn-products');
      const btnLogs = document.getElementById('nav-btn-logs');

      if (view === 'products') {
        productsDiv.classList.remove('hidden');
        logsDiv.classList.add('hidden');
        btnProducts.classList.add('active');
        btnLogs.classList.remove('active');
        window.location.hash = 'products';
      } else {
        productsDiv.classList.add('hidden');
        logsDiv.classList.remove('hidden');
        btnProducts.classList.remove('active');
        btnLogs.classList.add('active');
        window.location.hash = 'logs';
        fetchLogStatus();
      }
    }

    // ==========================================
    // 日志项目切换与查询
    // ==========================================
    function switchProject(projKey) {
      currentProject = projKey;
      const tabs = document.querySelectorAll('#project-tabs .project-tab-btn');
      tabs.forEach(t => {
        if (t.dataset.project === projKey) {
          t.classList.add('active');
        } else {
          t.classList.remove('active');
        }
      });
      fetchLogStatus();
    }

    function escapeHtml(str) {
      if (!str) return '';
      return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function renderTerminalLog(rawText) {
      const el = document.getElementById('log-terminal');
      const lines = rawText.split('\\n');
      const formatted = lines.map(line => {
        if (line.includes('Scrape Started') || line.includes('Crawler End Started')) {
          return `<span class="log-line-started">${escapeHtml(line)}</span>`;
        }
        if (line.includes('Finished (Exit: 0)')) {
          return `<span class="log-line-finished">${escapeHtml(line)}</span>`;
        }
        if (line.includes('Failed') || line.includes('Exit: 1') || line.includes('error:') || line.includes('Error')) {
          return `<span class="log-line-error">${escapeHtml(line)}</span>`;
        }
        if (line.startsWith('info:')) {
          return `<span class="log-line-info">${escapeHtml(line)}</span>`;
        }
        return escapeHtml(line);
      }).join('\\n');

      const shouldScroll = el.scrollTop + el.clientHeight >= el.scrollHeight - 60;
      el.innerHTML = formatted;
      if (shouldScroll) {
        el.scrollTop = el.scrollHeight;
      }
    }

    async function fetchLogStatus() {
      try {
        const base = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
        const res = await fetch(base + `api/status?project=${currentProject}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();

        document.getElementById('card-proj-name').textContent = data.name;
        document.getElementById('card-proj-badge').textContent = data.badge;
        document.getElementById('card-proj-total').textContent = (data.total > 0 ? (data.total + ' 项') : '--');
        document.getElementById('card-proj-detail').textContent = data.detail_stats || data.sub_desc;
        document.getElementById('card-proj-schedule').textContent = data.cron_schedule;
        document.getElementById('card-proj-updated').textContent = data.data_updated_at;
        document.getElementById('log-server-time').textContent = '服务器时间: ' + data.server_time;

        const logFiles = {
          end70: '/var/log/enddiscount.log',
          end: '/var/log/crawler-end.log',
          thelasthunt: '/var/log/crawler-thelasthunt.log'
        };
        document.getElementById('terminal-header-title').textContent = `📜 日志输出 (${logFiles[data.project] || data.project})`;
        renderTerminalLog(data.log);
      } catch (err) {
        document.getElementById('log-terminal').textContent = '获取日志状态失败: ' + err;
      }
    }

    async function triggerCurrentProjectScrape() {
      const msgEl = document.getElementById('trigger-msg');
      msgEl.textContent = `正在触发 [${currentProject}] 抓取...`;
      try {
        const base = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
        const res = await fetch(base + `api/trigger?project=${currentProject}`, { method: 'POST' });
        const resJson = await res.json();
        msgEl.textContent = `任务已启动，请查看下方日志更新`;
        setTimeout(fetchLogStatus, 1200);
        setTimeout(fetchLogStatus, 3200);
      } catch (err) {
        msgEl.textContent = '触发失败: ' + err;
      }
      setTimeout(() => { msgEl.textContent = ''; }, 6000);
    }

    function toggleAutoRefresh() {
      const enabled = document.getElementById('auto-refresh-chk').checked;
      if (logPollTimer) clearInterval(logPollTimer);
      if (enabled) {
        logPollTimer = setInterval(() => {
          if (currentMainView === 'logs') {
            fetchLogStatus();
          }
        }, 5000);
      }
    }

    // ==========================================
    // 原商品页面展示与过滤逻辑
    // ==========================================
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
      if (!name) return "Unknown";
      return name.trim().split(" ")[0];
    }

    function filterAndRender() {
      if (!window.products) return;

      let filtered = getTypeProducts();

      // Filter by discount
      if (currentDiscount !== 'all') {
        filtered = filtered.filter(p => p.discount === currentDiscount);
      }

      // Filter by brand (Case-insensitive)
      if (currentBrand !== 'all') {
        filtered = filtered.filter(p => {
          const brand = extractBrand(p.name);
          return brand.toLowerCase() === currentBrand.toLowerCase();
        });
      }

      // Filter by search
      if (currentSearch) {
        filtered = filtered.filter(p =>
          p.name.toLowerCase().includes(currentSearch.toLowerCase())
        );
      }

      renderProducts(filtered);
    }

    function renderProducts(items) {
      grid.innerHTML = '';
      countLabel.textContent = `${items.length} 件`;

      if (items.length === 0) {
        grid.innerHTML = '<p class="col-span-full text-center text-gray-400 py-10">未找到相关产品</p>';
        return;
      }

      items.forEach(product => {
        const card = document.createElement('div');
        card.className = 'product-card bg-white rounded-lg overflow-hidden flex flex-col shadow-md';

        const imgUrl = product.image_url || 'https://via.placeholder.com/300x400?text=No+Image';
        const formatter = new Intl.NumberFormat('zh-CN', {
          style: 'currency',
          currency: product.currency || 'CNY',
          maximumFractionDigits: 2
        });
        const price = product.discounted_price == null ? 'N/A' : formatter.format(product.discounted_price);
        const originalPrice = product.original_price == null ? 'N/A' : formatter.format(product.original_price);

        card.innerHTML = `
          <a href="${product.url}" target="_blank" class="block relative product-img overflow-hidden bg-gray-100">
            <img src="${imgUrl}" alt="${product.name}" class="w-full h-full object-cover object-center" loading="lazy">
            <div class="discount-badge absolute top-1 right-1 sm:top-2 sm:right-2 bg-red-500 text-white font-bold rounded px-1.5 py-0.5">
              ${product.discount || 'SALE'}
            </div>
          </a>
          <div class="product-info flex-1 flex flex-col">
            <h3 class="product-name font-medium text-gray-700 line-clamp-2 mb-1 sm:mb-2" title="${product.name}">${product.name}</h3>
            <div class="mt-auto flex items-baseline gap-1 sm:gap-2">
              <span class="product-price font-bold text-red-500">${price}</span>
              <span class="product-original text-gray-500 line-through">${originalPrice}</span>
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
        if (!brandsMap.has(lower)) {
          brandsMap.set(lower, brand);
        }
      });

      const sortedBrands = Array.from(brandsMap.values()).sort((a, b) =>
        a.toLowerCase().localeCompare(b.toLowerCase())
      );

      brandFilter.innerHTML = '<option value="all">所有品牌</option>';
      sortedBrands.forEach(brand => {
        const option = document.createElement('option');
        option.value = brand;
        option.textContent = brand;
        brandFilter.appendChild(option);
      });

      currentBrand = brandsMap.get(currentBrand.toLowerCase()) || 'all';
      brandFilter.value = currentBrand;
    }

    // 初始化商品数据
    if (window.products) {
      updateBrands();
      filterAndRender();

      if (window.updateTime) {
        document.getElementById('update-time').textContent = `📅 最后更新: ${window.updateTime}`;
      }

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

      brandFilter.addEventListener('change', (e) => {
        currentBrand = e.target.value;
        filterAndRender();
      });

      searchInput.addEventListener('input', (e) => {
        currentSearch = e.target.value;
        filterAndRender();
      });
    } else {
      grid.innerHTML = '<p class="col-span-full text-center text-gray-400 py-10">暂未发现商品数据，请在日志中心点击“立即抓取一次”。</p>';
    }

    // 回到顶部按钮逻辑
    const backToTopBtn = document.getElementById('back-to-top');
    window.addEventListener('scroll', () => {
      if (window.scrollY > 300) {
        backToTopBtn.classList.add('show');
      } else {
        backToTopBtn.classList.remove('show');
      }
    });
    backToTopBtn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    // Hash 路由初始化
    if (window.location.hash === '#logs') {
      switchMainView('logs');
    } else {
      switchMainView('products');
    }

    toggleAutoRefresh();
  </script>
</body>
</html>
"""


class MultiProjectLogHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 静默日志请求，避免终端刷屏
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

        # 首页与主要视图
        if path in ("", "/log", "/logs", "/index.html") or parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        # 提供 data.js 静态文件
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
                self.wfile.write("window.products = []; window.updateTime = '暂无数据';".encode("utf-8"))

        # 状态与日志 API
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
            self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.endswith("/api/trigger"):
            query = parse_qs(parsed.query)
            proj = query.get("project", ["end70"])[0]
            success, message = trigger_scrape_background(proj)

            self.send_response(200 if success else 400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            resp = {
                "status": "triggered" if success else "failed",
                "project": proj,
                "message": message,
            }
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


def run():
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, MultiProjectLogHandler)
    print(f"Log & Product Web server running at http://{HOST}:{PORT}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
