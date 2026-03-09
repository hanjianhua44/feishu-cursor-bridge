"""
Feishu Bridge Monitor — WebSocket long connection + auto-trigger Cursor Composer.

Flow:
  1. Receive message from Feishu via WebSocket
  2. Commands (/mode, /model, etc.) → handled directly, reply to Feishu
  3. Normal messages → paste into Cursor Composer with [飞书] prefix, send
  4. Cursor Agent (via feishu-bridge.mdc rule) processes and replies back to Feishu
"""

import json
import os
import subprocess
import sys
import time
import logging
import threading
import requests
import lark_oapi as lark
from lark_oapi import ws, LogLevel
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from pathlib import Path
from datetime import datetime

from formatter import (
    parse_command,
    format_reply_text,
    VALID_MODES,
    VALID_MODELS,
    DEFAULT_MODEL,
    MODE_DESCRIPTIONS,
    HELP_TEXT,
)

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "state.json"
ENV_FILE = SCRIPT_DIR / ".env"

def _load_env():
    """Load variables from .env file if it exists."""
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BASE_URL = "https://open.feishu.cn/open-apis"

if not APP_ID or not APP_SECRET:
    print("ERROR: FEISHU_APP_ID and FEISHU_APP_SECRET must be set.")
    print("Set them as environment variables or in .env file.")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("feishu-bridge")


# ── Feishu API sender ──────────────────────────────────────────────

class FeishuSender:
    def __init__(self):
        self._token: str = ""
        self._token_expires: float = 0

    @property
    def token(self) -> str:
        if time.time() >= self._token_expires:
            self._refresh_token()
        return self._token

    def _refresh_token(self):
        resp = requests.post(
            f"{BASE_URL}/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200) - 60
        log.info("Refreshed tenant access token")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def send_message(self, chat_id: str, text: str):
        resp = requests.post(
            f"{BASE_URL}/im/v1/messages",
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": format_reply_text(text),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


# ── State management ────────────────────────────────────────────────

DEFAULT_STATE = {
    "chat_id": "",
    "user_open_id": "",
    "current_mode": "agent",
    "current_model": "",
    "last_processed_ts": "",
    "pending_messages": [],
    "context_files": [],
    "conversation_history": [],
    "open_new_composer": False,
}

MAX_HISTORY = 20


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return dict(DEFAULT_STATE)


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── Cursor auto-trigger ─────────────────────────────────────────────

_trigger_lock = threading.Lock()
_trigger_pending = False
TRIGGER_DEBOUNCE = 3


def _clipboard_paste(text: str):
    """Set clipboard via PowerShell and paste with Ctrl+V (bypasses IME)."""
    import pyautogui
    escaped = text.replace("'", "''")
    subprocess.run(
        ["powershell", "-Command", f"Set-Clipboard -Value '{escaped}'"],
        capture_output=True, timeout=5,
    )
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "v")


def _find_cursor_hwnd():
    """Find the Cursor IDE window handle."""
    import ctypes
    user32 = ctypes.windll.user32
    hwnds = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if "Cursor" in buf.value:
                    hwnds.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)
    )
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return hwnds[0] if hwnds else None


def trigger_cursor_agent(message_text: str, open_new: bool):
    """
    Activate Cursor and paste the message into Composer.
    - open_new=True  → Ctrl+Shift+I (new Composer)
    - open_new=False → Ctrl+L (focus existing Composer)
    """
    global _trigger_pending
    try:
        import pyautogui
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = _find_cursor_hwnd()
        if not hwnd:
            log.warning("Cursor window not found, skipping auto-trigger")
            return

        prompt = f"[飞书] {message_text}"

        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.8)

        if open_new:
            pyautogui.hotkey("ctrl", "shift", "i")
            time.sleep(1.5)
            log.info("Opened new Composer")
        else:
            time.sleep(0.5)
            log.info("Using current Composer")

        _clipboard_paste(prompt)
        time.sleep(0.5)

        pyautogui.press("enter")

        log.info("Sent to Composer: %s", prompt[:80])

    except Exception as e:
        log.error("Auto-trigger failed: %s", e)
    finally:
        with _trigger_lock:
            _trigger_pending = False


def schedule_trigger(message_text: str, open_new: bool):
    """Debounced trigger — waits TRIGGER_DEBOUNCE seconds to batch rapid messages."""
    global _trigger_pending
    with _trigger_lock:
        if _trigger_pending:
            return
        _trigger_pending = True

    def _delayed():
        time.sleep(TRIGGER_DEBOUNCE)
        trigger_cursor_agent(message_text, open_new)

    t = threading.Thread(target=_delayed, daemon=True)
    t.start()


# ── Command handling ────────────────────────────────────────────────

def handle_command(feishu: FeishuSender, state: dict, cmd: str, arg: str | None) -> str | None:
    if cmd == "/mode":
        if not arg or arg not in VALID_MODES:
            return f"无效模式。可选: {', '.join(VALID_MODES)}"
        state["current_mode"] = arg
        save_state(state)
        return f"已切换到 {MODE_DESCRIPTIONS[arg]}"

    if cmd == "/model":
        if not arg:
            models_str = " | ".join(sorted(VALID_MODELS))
            return f"请指定模型名称。可用模型: {models_str}\n例如: /model claude-4.6-opus"
        if arg not in VALID_MODELS:
            models_str = " | ".join(sorted(VALID_MODELS))
            return f"不支持的模型: {arg}\n可用模型: {models_str}"
        state["current_model"] = arg
        save_state(state)
        return f"已切换模型为 {arg}"

    if cmd == "/status":
        pending_count = len(state.get("pending_messages", []))
        ctx_files = state.get("context_files", [])
        lines = [
            f"当前模式: {state['current_mode']}",
            f"当前模型: {state.get('current_model') or '(默认)'}",
            f"待处理消息: {pending_count}",
            f"上下文文件: {', '.join(ctx_files) if ctx_files else '(无)'}",
            f"对话历史: {len(state.get('conversation_history', []))} 条",
        ]
        return "\n".join(lines)

    if cmd == "/help":
        return HELP_TEXT

    if cmd == "/new":
        state["pending_messages"] = []
        state["context_files"] = []
        state["conversation_history"] = []
        state["open_new_composer"] = True
        save_state(state)
        mode = state.get("current_mode", "agent")
        model = state.get("current_model") or "(默认)"
        return f"已新建对话，下一条消息将在新 Composer 中打开\n当前模式: {mode} | 模型: {model}"

    if cmd == "/clear":
        state["pending_messages"] = []
        state["context_files"] = []
        state["conversation_history"] = []
        state["current_mode"] = "agent"
        state["current_model"] = DEFAULT_MODEL
        state["open_new_composer"] = True
        save_state(state)
        return f"已完全重置，下一条消息将在新 Composer 中打开\n模式: agent | 模型: {DEFAULT_MODEL}"

    if cmd == "/context":
        if not arg:
            return "请指定文件路径，例如: /context src/utils.py"
        ctx = state.setdefault("context_files", [])
        if arg not in ctx:
            ctx.append(arg)
            save_state(state)
            return f"已添加上下文文件: {arg}"
        return f"文件已在上下文中: {arg}"

    return f"未知指令: {cmd}\n输入 /help 查看可用指令"


# ── Message event handler ───────────────────────────────────────────

feishu = FeishuSender()
state = load_state()


def on_message_receive(data: P2ImMessageReceiveV1):
    """Called when a new message is received via WebSocket."""
    global state

    try:
        event = data.event
        msg = event.message
        msg_sender = event.sender

        if msg_sender and msg_sender.sender_type == "app":
            return

        chat_id = msg.chat_id
        msg_type = msg.message_type
        create_time_ms = str(msg.create_time or int(time.time() * 1000))
        message_id = msg.message_id or ""
        sender_id = ""
        if msg_sender and msg_sender.sender_id:
            sender_id = msg_sender.sender_id.open_id or ""

        if not state.get("chat_id") and chat_id:
            state["chat_id"] = chat_id
            save_state(state)
            log.info("Discovered chat_id: %s", chat_id)

        content_str = msg.content or ""
        text = ""
        try:
            content = json.loads(content_str)
            if msg_type == "text":
                text = content.get("text", "").strip()
            elif msg_type == "post":
                post = content.get("post", content)
                for lang in ("zh_cn", "en_us", "ja_jp"):
                    lang_post = post.get(lang)
                    if lang_post:
                        parts = []
                        for para in lang_post.get("content", []):
                            for elem in para:
                                if elem.get("tag") == "text":
                                    parts.append(elem.get("text", ""))
                        text = "".join(parts).strip()
                        break
            else:
                text = content_str
        except (json.JSONDecodeError, TypeError):
            text = content_str

        if not text:
            return

        ts_sec = int(create_time_ms) // 1000
        log.info("Message [%s]: %s",
                 datetime.fromtimestamp(ts_sec).strftime("%H:%M:%S"),
                 text[:100])

        cmd, arg = parse_command(text)
        if cmd:
            reply = handle_command(feishu, state, cmd, arg)
            if reply and chat_id:
                feishu.send_message(chat_id, reply)
                log.info("Command %s handled, replied", cmd)
        else:
            # Determine if we should open a new Composer
            open_new = state.get("open_new_composer", False)
            if open_new:
                state["open_new_composer"] = False

            state.setdefault("pending_messages", []).append({
                "text": text,
                "ts": create_time_ms,
                "sender_id": sender_id,
                "message_id": message_id,
            })
            state["last_processed_ts"] = create_time_ms
            save_state(state)
            log.info("Queued (total: %d), triggering Cursor (new=%s)",
                     len(state["pending_messages"]), open_new)
            schedule_trigger(text, open_new)

    except Exception as e:
        log.error("Error handling message: %s", e, exc_info=True)


# ── Main ────────────────────────────────────────────────────────────

def main():
    global state
    state = load_state()

    log.info("Starting Feishu Bridge (WebSocket long connection)...")
    log.info("Mode: %s | Model: %s",
             state.get("current_mode", "agent"),
             state.get("current_model") or "(default)")

    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(on_message_receive) \
        .build()

    cli = ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=event_handler,
        log_level=LogLevel.INFO,
    )

    log.info("Connecting to Feishu WebSocket...")
    cli.start()


if __name__ == "__main__":
    main()
