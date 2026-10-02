# -*- coding: utf-8 -*-
"""Brasseur 日志收集服务端：接收各客户端上报的问答日志，并提供网页集中查看。

零第三方依赖，直接 python log_server.py 或打包成 EXE 运行。
日志保存在本文件/EXE 同目录的 logs.jsonl。
"""
import json
import os
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_PATH = os.path.join(BASE_DIR, "logs.jsonl")
PORT = 8520

_write_lock = threading.Lock()

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Brasseur 问答日志</title>
<style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #f7f7f8; color: #1a1a1a; }
    .top { background: #fff; border-bottom: 1px solid #ececec; padding: 16px 24px; display: flex; align-items: center; gap: 16px; position: sticky; top: 0; }
    .top h1 { font-size: 18px; font-weight: 600; background: linear-gradient(135deg, #6366f1, #06b6d4); -webkit-background-clip: text; background-clip: text; color: transparent; }
    .top .count { font-size: 13px; color: #999; }
    .top input { margin-left: auto; border: 1px solid #e5e7eb; border-radius: 10px; padding: 8px 14px; font-size: 14px; width: 260px; outline: none; }
    .top input:focus { border-color: #6366f1; }
    table { width: calc(100% - 48px); margin: 20px 24px 40px; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
    th, td { text-align: left; padding: 12px 14px; font-size: 13.5px; border-bottom: 1px solid #f0f0f2; vertical-align: top; }
    th { background: #fafafa; color: #666; font-weight: 600; white-space: nowrap; }
    td.time { white-space: nowrap; color: #999; font-size: 12.5px; }
    td.user { white-space: nowrap; color: #6366f1; font-weight: 600; }
    td.real { color: #444; white-space: pre-wrap; word-break: break-word; }
    td.real.empty { color: #bbb; }
    tr:last-child td { border-bottom: none; }
    .badge { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 99px; }
    .badge.on { background: #ecfdf5; color: #059669; }
    .badge.off { background: #f4f4f5; color: #999; }
</style>
</head>
<body>
<div class="top">
    <h1>Brasseur 问答日志</h1>
    <span class="count" id="count"></span>
    <input id="kw" placeholder="关键词筛选（用户 / 问题 / 回答）…">
</div>
<table>
    <thead><tr><th>时间</th><th>用户</th><th>问题</th><th>真实回答（已被撤回）</th><th>状态</th></tr></thead>
    <tbody id="rows"></tbody>
</table>
<script>
    let logs = [];
    async function refresh() {
        try {
            const res = await fetch('/logs');
            logs = await res.json();
            render();
        } catch (e) {}
    }
    function esc(s) {
        return String(s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    }
    function render() {
        const kw = document.getElementById('kw').value.trim().toLowerCase();
        const list = kw ? logs.filter(x =>
            (x.user + x.question + x.real_answer).toLowerCase().includes(kw)
        ) : logs;
        document.getElementById('count').textContent = '共 ' + list.length + ' 条';
        document.getElementById('rows').innerHTML = list.slice().reverse().map(x => `
            <tr>
                <td class="time">${esc(x.time)}</td>
                <td class="user">${esc(x.user)}</td>
                <td>${esc(x.question)}</td>
                <td class="real${x.real_answer ? '' : ' empty'}">${x.real_answer ? esc(x.real_answer) : '（离线，无真实回答）'}</td>
                <td><span class="badge ${x.online ? 'on' : 'off'}">${x.online ? '在线' : '离线'}</span></td>
            </tr>`).join('');
    }
    document.getElementById('kw').addEventListener('input', render);
    refresh();
    setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音访问日志
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/logs"):
            logs = []
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                logs.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            self._send(200, json.dumps(logs, ensure_ascii=False),
                       "application/json; charset=utf-8")
        else:
            self._send(200, PAGE)

    def do_POST(self):
        if not self.path.startswith("/log"):
            self._send(404, "{}")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            item = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send(400, "{}")
            return
        item = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": str(item.get("user", ""))[:64],
            "question": str(item.get("question", ""))[:2000],
            "real_answer": str(item.get("real_answer", ""))[:8000],
            "shown_answer": str(item.get("shown_answer", ""))[:200],
            "online": bool(item.get("online")),
        }
        with _write_lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        self._send(200, "{}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("Brasseur 日志服务端已启动")
    print("查看页面: http://127.0.0.1:%d" % PORT)
    print("局域网访问: http://<本机IP>:%d" % PORT)
    print("日志文件: %s" % LOG_PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
