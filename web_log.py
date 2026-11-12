#!/usr/bin/env python3
import json
import os
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = "/var/log/enddiscount.log"
DATA_FILE = os.path.join(BASE_DIR, "endclothing_70off.json")
RUN_SCRIPT = os.path.join(BASE_DIR, "run_cron.sh")
HOST = "127.0.0.1"
PORT = 8088


def get_log_tail(lines=150):
    if not os.path.exists(LOG_FILE):
        return f"日志文件 {LOG_FILE} 尚不存在。"
    try:
        res = subprocess.run(
            ["tail", "-n", str(lines), LOG_FILE],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return res.stdout or "(日志内容为空)"
    except Exception as e:
        return f"读取日志失败: {e}"


def get_status_data():
    total = 0
    men = 0
    women = 0
    last_mod = "未知"
    if os.path.exists(DATA_FILE):
        try:
            mtime = os.path.getmtime(DATA_FILE)
            last_mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
                total = len(items)
                men = sum(1 for x in items if x.get("type") == "men")
                women = sum(1 for x in items if x.get("type") == "women")
        except Exception:
            pass

    return {
        "total": total,
        "men": men,
        "women": women,
        "data_updated_at": last_mod,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "log": get_log_tail(150),
    }


def trigger_scrape_background():
    def _run():
        if os.path.exists(RUN_SCRIPT):
            subprocess.run(["/bin/bash", RUN_SCRIPT], timeout=30)
    t = threading.Thread(target=_run, daemon=True)
    t.start()


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>END. 折扣监控日志中心</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --accent: #58a6ff;
      --success: #3fb950;
      --terminal-bg: #030712;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 20px;
    }
    .container { max-width: 1080px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      padding-bottom: 15px;
      border-bottom: 1px solid var(--border);
    }
    h1 { font-size: 20px; font-weight: 600; color: #fff; }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 15px;
      margin-bottom: 20px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }
    .card .title { font-size: 13px; color: #8b949e; margin-bottom: 6px; }
    .card .val { font-size: 24px; font-weight: bold; color: #fff; }
    .card .sub { font-size: 12px; color: var(--success); margin-top: 4px; }
    .toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      flex-wrap: wrap;
      gap: 10px;
    }
    .btn-group { display: flex; gap: 10px; align-items: center; }
    button {
      background: #238636;
      color: #fff;
      border: none;
      padding: 7px 14px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      transition: background 0.2s;
    }
    button:hover { background: #2ea043; }
    button.secondary {
      background: #21262d;
      border: 1px solid var(--border);
      color: var(--text);
    }
    button.secondary:hover { background: #30363d; }
    .auto-refresh {
      font-size: 13px;
      color: #8b949e;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .terminal {
      background: var(--terminal-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: 13px;
      line-height: 1.5;
      color: #38bdf8;
      height: 520px;
      overflow-y: auto;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .log-line-started { color: #facc15; font-weight: bold; }
    .log-line-finished { color: #4ade80; }
    .log-line-error { color: #f87171; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>END. 折扣监控日志中心 (RakSmart)</h1>
      <span style="font-size: 13px; color: #8b949e;" id="time-badge">系统时间: --</span>
    </header>

    <div class="stats-grid">
      <div class="card">
        <div class="title">当前折扣商品总数</div>
        <div class="val" id="stat-total">--</div>
        <div class="sub">60% ~ 70% off 档位</div>
      </div>
      <div class="card">
        <div class="title">分类商品统计</div>
        <div class="val" style="font-size: 18px;" id="stat-split">男装: -- | 女装: --</div>
        <div class="sub">实时分类结构</div>
      </div>
      <div class="card">
        <div class="title">调度策略</div>
        <div class="val" style="font-size: 18px; color: var(--accent);">每 5 分钟</div>
        <div class="sub">Crontab 准秒调度</div>
      </div>
      <div class="card">
        <div class="title">数据更新状态</div>
        <div class="val" style="font-size: 14px; margin-top: 4px;" id="stat-updated">--</div>
        <div class="sub">变动时推送钉钉</div>
      </div>
    </div>

    <div class="toolbar">
      <div class="btn-group">
        <button class="secondary" onclick="fetchStatus()">🔄 刷新日志</button>
        <button onclick="triggerScrape()">⚡ 立即抓取一次</button>
        <span id="trigger-hint" style="font-size: 12px; color: var(--accent);"></span>
      </div>
      <label class="auto-refresh">
        <input type="checkbox" id="auto-refresh-toggle" checked onchange="toggleAutoRefresh()"> 5 秒自动轮询
      </label>
    </div>

    <pre class="terminal" id="terminal-content">正在载入日志...</pre>
  </div>

  <script>
    let timer = null;

    function renderLog(text) {
      const el = document.getElementById('terminal-content');
      const lines = text.split('\\n');
      const formatted = lines.map(line => {
        if (line.includes('Scrape Started')) return `<span class="log-line-started">${escapeHtml(line)}</span>`;
        if (line.includes('Finished (Exit: 0)')) return `<span class="log-line-finished">${escapeHtml(line)}</span>`;
        if (line.includes('Failed') || line.includes('Exit: 1')) return `<span class="log-line-error">${escapeHtml(line)}</span>`;
        return escapeHtml(line);
      }).join('\\n');
      
      const shouldScroll = el.scrollTop + el.clientHeight >= el.scrollHeight - 50;
      el.innerHTML = formatted;
      if (shouldScroll) {
        el.scrollTop = el.scrollHeight;
      }
    }

    function escapeHtml(str) {
      return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    async function fetchStatus() {
      try {
        const base = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
        const res = await fetch(base + 'api/status');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        document.getElementById('stat-total').textContent = data.total + ' 件';
        document.getElementById('stat-split').textContent = `男装: ${data.men} | 女装: ${data.women}`;
        document.getElementById('stat-updated').textContent = data.data_updated_at;
        document.getElementById('time-badge').textContent = '系统时间: ' + data.server_time;
        renderLog(data.log);
      } catch (e) {
        document.getElementById('terminal-content').textContent = '获取日志失败: ' + e;
      }
    }

    async function triggerScrape() {
      const hint = document.getElementById('trigger-hint');
      hint.textContent = '正在触发抓取...';
      try {
        const base = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
        await fetch(base + 'api/trigger', { method: 'POST' });
        hint.textContent = '抓取任务已启动，请等待日志刷新';
        setTimeout(fetchStatus, 1500);
        setTimeout(fetchStatus, 3500);
      } catch (e) {
        hint.textContent = '触发失败: ' + e;
      }
      setTimeout(() => { hint.textContent = ''; }, 6000);
    }

    function toggleAutoRefresh() {
      const enabled = document.getElementById('auto-refresh-toggle').checked;
      if (timer) clearInterval(timer);
      if (enabled) {
        timer = setInterval(fetchStatus, 5000);
      }
    }

    fetchStatus();
    toggleAutoRefresh();
  </script>
</body>
</html>
"""


class SimpleLogHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 静默日志请求，避免刷屏
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in ("", "/log", "/logs") or parsed.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif path.endswith("/api/status"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            data = get_status_data()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path.endswith("/api/trigger"):
            trigger_scrape_background()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"status": "triggered"}')
        else:
            self.send_response(404)
            self.end_headers()


def run():
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, SimpleLogHandler)
    print(f"Log Web server running at http://{HOST}:{PORT}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run()
