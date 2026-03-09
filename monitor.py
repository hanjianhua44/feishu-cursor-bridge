"""
Feishu Bridge Monitor — WebSocket long connection + auto-trigger Cursor Composer.

Flow:
  1. Receive message from Feishu via WebSocket
  2. Commands (/mode, /model, etc.) → handled directly, reply to Feishu
  3. Normal messages → paste into Cursor Composer with [飞书] prefix, send
  4. Cursor Agent (via feishu-bridge.mdc rule) processes and replies back to Feishu
"""

import atexit
import json
import os
import signal
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
    parse_feishu_message,
    format_reply_text,
    VALID_MODES,
    VALID_MODELS,
    DEFAULT_MODEL,
    MODE_DESCRIPTIONS,
    HELP_TEXT,
)

SCRIPT_DIR = Path(__file__).parent
STATE_FILE = SCRIPT_DIR / "state.json"
STATE_LOCK_FILE = SCRIPT_DIR / "state.lock"
ENV_FILE = SCRIPT_DIR / ".env"
PID_FILE = SCRIPT_DIR / "monitor.pid"

# ── Timing constants (seconds) ────────────────────────────────────
DELAY_WINDOW_ACTIVATE = 0.8
DELAY_COMPOSER_OPEN = 1.5
DELAY_COMPOSER_REUSE = 0.5
DELAY_AFTER_PASTE = 0.5
DELAY_CLIPBOARD_SET = 0.3
TRIGGER_DEBOUNCE = 3
REPLY_TIMEOUT = 120


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


# ── PID lock ─────────────────────────────────────────────────────

def _acquire_pid_lock():
    """Ensure only one monitor instance is running. Kill stale process if needed."""
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _is_process_alive(old_pid):
                log.error("Another monitor is already running (PID %d). Exiting.", old_pid)
                sys.exit(1)
            else:
                log.warning("Stale PID file found (PID %d not running), taking over.", old_pid)
        except (ValueError, OSError):
            pass

    PID_FILE.write_text(str(os.getpid()))
    atexit.register(_release_pid_lock)


def _release_pid_lock():
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def _is_process_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        return str(pid) in result.stdout
    except Exception:
        return False


# ── Feishu API sender ──────────────────────────────────────────────

class FeishuSender:
    def __init__(self):
        self._token: str = ""
        self._token_expires: float = 0
        self._lock = threading.Lock()

    @property
    def token(self) -> str:
        with self._lock:
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


# ── State management (with file lock) ─────────────────────────────

_state_lock = threading.Lock()

DEFAULT_STATE = {
    "chat_id": "",
    "user_open_id": "",
    "current_mode": "agent",
    "current_model": DEFAULT_MODEL,
    "last_processed_ts": "",
    "pending_messages": [],
    "context_files": [],
    "conversation_history": [],
    "open_new_composer": False,
    "last_trigger_ts": 0,
}

MAX_HISTORY = 20


def load_state() -> dict:
    with _state_lock:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in DEFAULT_STATE.items():
                    data.setdefault(k, v)
                return data
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load state, using defaults: %s", e)
        return dict(DEFAULT_STATE)


def save_state(state: dict):
    with _state_lock:
        tmp = STATE_FILE.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            tmp.replace(STATE_FILE)
        except OSError as e:
            log.error("Failed to save state: %s", e)
            if tmp.exists():
                tmp.unlink(missing_ok=True)


# ── Cursor auto-trigger ─────────────────────────────────────────────

_trigger_lock = threading.Lock()
_trigger_timer: threading.Timer | None = None
_trigger_open_new = False


def _set_clipboard(text: str):
    """Set clipboard via PowerShell single-quoted string."""
    escaped = text.replace("'", "''")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Set-Clipboard -Value '{escaped}'"],
        capture_output=True, timeout=5,
    )


def _clipboard_paste(text: str):
    """Set clipboard and paste with Ctrl+V."""
    import pyautogui

    _set_clipboard(text)
    time.sleep(DELAY_CLIPBOARD_SET)
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


def _build_merged_prompt(state: dict) -> str:
    """Build prompt from all pending messages instead of just the latest one."""
    pending = state.get("pending_messages", [])
    if not pending:
        return ""
    if len(pending) == 1:
        return f"[飞书] {pending[0]['text']}"
    texts = [m["text"] for m in pending]
    return "[飞书] " + "\n".join(texts)


def trigger_cursor_agent(open_new: bool):
    """
    Activate Cursor and paste merged pending messages into Composer.
    Returns True on success, False on failure.
    """
    try:
        import pyautogui
        import ctypes

        st = load_state()
        prompt = _build_merged_prompt(st)
        if not prompt:
            log.info("No pending messages to trigger")
            return True

        user32 = ctypes.windll.user32
        hwnd = _find_cursor_hwnd()
        if not hwnd:
            log.warning("Cursor window not found")
            return False

        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(DELAY_WINDOW_ACTIVATE)

        if open_new:
            pyautogui.hotkey("ctrl", "shift", "i")
            time.sleep(DELAY_COMPOSER_OPEN)
            log.info("Opened new Composer")
        else:
            time.sleep(DELAY_COMPOSER_REUSE)
            log.info("Using current Composer")

        _clipboard_paste(prompt)
        time.sleep(DELAY_AFTER_PASTE)

        pyautogui.press("enter")

        st["last_trigger_ts"] = time.time()
        save_state(st)

        log.info("Sent to Composer: %s", prompt[:80])
        _start_reply_watchdog(st.get("chat_id", ""))
        return True

    except Exception as e:
        log.error("Auto-trigger failed: %s", e)
        return False


def schedule_trigger(open_new: bool):
    """Debounced trigger — resets timer on each call to batch rapid messages."""
    global _trigger_timer, _trigger_open_new

    with _trigger_lock:
        if open_new:
            _trigger_open_new = True

        if _trigger_timer is not None:
            _trigger_timer.cancel()

        def _fire():
            global _trigger_timer, _trigger_open_new
            with _trigger_lock:
                use_new = _trigger_open_new
                _trigger_open_new = False
            success = trigger_cursor_agent(use_new)
            if not success:
                _notify_trigger_failure()
            with _trigger_lock:
                _trigger_timer = None

        _trigger_timer = threading.Timer(TRIGGER_DEBOUNCE, _fire)
        _trigger_timer.daemon = True
        _trigger_timer.start()


def _notify_trigger_failure():
    """Send failure notification to Feishu when Cursor trigger fails."""
    try:
        st = load_state()
        chat_id = st.get("chat_id")
        if chat_id:
            feishu.send_message(
                chat_id,
                "⚠ Cursor 触发失败：未找到 Cursor 窗口或自动化出错。\n"
                "请确认 Cursor IDE 已打开，消息已保存在待处理队列中。\n"
                "你也可以在 Cursor 中手动输入「检查飞书」来处理。"
            )
    except Exception as e:
        log.error("Failed to send trigger failure notification: %s", e)


# ── Reply watchdog ──────────────────────────────────────────────────

def _start_reply_watchdog(chat_id: str):
    """Start a timer that checks if AI replied within REPLY_TIMEOUT seconds."""
    if not chat_id:
        return

    trigger_ts = time.time()

    def _check():
        try:
            st = load_state()
            pending = st.get("pending_messages", [])
            if not pending:
                return
            last_trigger = st.get("last_trigger_ts", 0)
            if last_trigger > trigger_ts:
                return
            if time.time() - trigger_ts >= REPLY_TIMEOUT and pending:
                log.warning("Reply timeout: %d pending messages not processed after %ds",
                            len(pending), REPLY_TIMEOUT)
                feishu.send_message(
                    chat_id,
                    f"⏰ 已等待 {REPLY_TIMEOUT} 秒仍未收到 AI 回复。\n"
                    f"待处理消息: {len(pending)} 条\n"
                    "可能原因：Cursor Agent 未响应或 MCP 发送失败。\n"
                    "你可以在 Cursor 中手动输入「检查飞书」来处理。"
                )
        except Exception as e:
            log.error("Reply watchdog error: %s", e)

    t = threading.Timer(REPLY_TIMEOUT, _check)
    t.daemon = True
    t.start()


# ── Command handling ────────────────────────────────────────────────

def handle_command(feishu_sender: FeishuSender, state: dict, cmd: str, arg: str | None) -> str | None:
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
            f"当前模型: {state.get('current_model') or DEFAULT_MODEL}",
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
        model = state.get("current_model") or DEFAULT_MODEL
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


def _extract_text(msg, msg_type: str) -> str:
    """Extract plain text from Feishu message using formatter where possible."""
    content_str = msg.content or ""
    try:
        content = json.loads(content_str)
    except (json.JSONDecodeError, TypeError):
        return content_str

    if msg_type == "text":
        return content.get("text", "").strip()

    if msg_type == "post":
        parsed = parse_feishu_message({"msg_type": "post", "body": {"content": content_str}})
        return (parsed or "").strip()

    return content_str


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

        text = _extract_text(msg, msg_type)
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
            schedule_trigger(open_new)

    except Exception as e:
        log.error("Error handling message: %s", e, exc_info=True)


# ── Main ────────────────────────────────────────────────────────────

def main():
    global state

    _acquire_pid_lock()
    state = load_state()

    log.info("Starting Feishu Bridge (WebSocket long connection)...")
    log.info("Mode: %s | Model: %s | PID: %d",
             state.get("current_mode", "agent"),
             state.get("current_model") or DEFAULT_MODEL,
             os.getpid())

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