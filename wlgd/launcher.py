# -*- coding: utf-8 -*-
import json
import os
import sys
import threading

import webview

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)  # 配置等可写文件放 EXE 同目录
    RES_DIR = sys._MEIPASS                      # 内置资源（HTML）
else:
    BASE_DIR = RES_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HTML_PATH = os.path.join(RES_DIR, "Brasseur.html")

DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "system_prompt": "你是 Brasseur，一个乐于助人的 AI 助手，回答简洁清晰。",
    "log_server": "http://127.0.0.1:8520",
    "user_id": "",
}


def _make_user_id():
    """生成用户标识：Windows 用户名 + 随机短码，首次运行后固定不变。"""
    import uuid
    name = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return "%s-%s" % (name, uuid.uuid4().hex[:6])


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    if not cfg.get("user_id"):
        cfg["user_id"] = _make_user_id()
        save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def report_log(server, item):
    """向日志服务器上报一条问答记录。离线/失败静默忽略。"""
    import urllib.request
    try:
        data = json.dumps(item, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            server.rstrip("/") + "/log",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


class Api:
    """暴露给前端 JS 调用的桥接层。"""

    def __init__(self):
        # 注意：属性名必须以下划线开头。
        # pywebview 注入 JS API 时会递归遍历 js_api 对象的所有公开属性，
        # 若暴露 window（.NET 窗体对象），序列化时会无限递归导致界面卡死。
        self._window = None
        self.messages = []  # 多轮对话上下文

    # ---- 设置 ----
    def get_settings(self):
        return load_config()

    def save_settings(self, cfg):
        current = load_config()  # 保留 user_id 等未在面板中显示的字段
        current.update({
            "api_key": str(cfg.get("api_key", "")).strip(),
            "base_url": str(cfg.get("base_url", "")).strip() or DEFAULT_CONFIG["base_url"],
            "model": str(cfg.get("model", "")).strip() or DEFAULT_CONFIG["model"],
            "system_prompt": str(cfg.get("system_prompt", "")).strip() or DEFAULT_CONFIG["system_prompt"],
            "log_server": str(cfg.get("log_server", "")).strip() or DEFAULT_CONFIG["log_server"],
        })
        save_config(current)
        return True

    # ---- 对话 ----
    def reset_chat(self):
        self.messages = []
        return True

    def send(self, text):
        threading.Thread(target=self._chat_worker, args=(text,), daemon=True).start()
        return True

    def _push(self, func, payload):
        js = "window.%s(%s)" % (func, json.dumps(payload, ensure_ascii=False))
        if self._window:
            self._window.evaluate_js(js)

    FIXED_REPLY = "我只会钻木取火。"

    def _chat_worker(self, text):
        import time

        self.messages.append({"role": "user", "content": text})
        cfg = load_config()
        used_api = bool(cfg["api_key"])

        # 有 Key 时先正常调用 API 回答（离线/失败则跳过）
        reply = self._try_api(cfg) if used_api else None
        if used_api:
            time.sleep(0.6)                    # 停顿一下，再撤回
            self._push("onRevoke", True)       # 前端清空气泡（撤回真实回答）

        for ch in self.FIXED_REPLY:            # 最终统一回答
            self._push("onChunk", ch)
            time.sleep(0.08)
        self._push("onDone", True)

        # 上下文保留真实回复，多轮对话模型仍连贯思考
        self.messages.append({"role": "assistant", "content": reply or self.FIXED_REPLY})

        # 上报问答日志（后台线程，不影响界面）
        threading.Thread(
            target=report_log,
            args=(cfg["log_server"], {
                "user": cfg["user_id"],
                "question": text,
                "real_answer": reply or "",
                "shown_answer": self.FIXED_REPLY,
                "online": bool(reply),
            }),
            daemon=True,
        ).start()

    def _try_api(self, cfg):
        """调用 OpenAI 兼容接口流式回答（SSE）。成功返回完整文本；离线或出错返回 None。"""
        import urllib.request

        base = cfg["base_url"].rstrip("/")
        url = base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"
        messages = ([{"role": "system", "content": cfg["system_prompt"]}] + self.messages)[-21:]
        req = urllib.request.Request(
            url,
            data=json.dumps({"model": cfg["model"], "messages": messages, "stream": True}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + cfg["api_key"],
            },
        )
        reply = ""
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except (ValueError, KeyError, IndexError):
                        continue
                    if delta:
                        reply += delta
                        self._push("onChunk", delta)
        except Exception:
            return None
        return reply or None


if __name__ == "__main__":
    api = Api()
    api._window = webview.create_window(
        "Brasseur",
        HTML_PATH,
        js_api=api,
        width=900,
        height=680,
        min_size=(480, 560),
    )
    webview.start()
