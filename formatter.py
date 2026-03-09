"""
Feishu message format conversion utilities.
Handles parsing incoming Feishu messages and formatting outgoing replies.
"""

import json
import re
from typing import Optional


def parse_feishu_message(msg: dict) -> Optional[str]:
    """Extract plain text from a Feishu message object."""
    msg_type = msg.get("msg_type", "")
    body = msg.get("body", {})
    content_str = body.get("content", "")

    if not content_str:
        return None

    try:
        content = json.loads(content_str)
    except (json.JSONDecodeError, TypeError):
        return content_str if isinstance(content_str, str) else None

    if msg_type == "text":
        return content.get("text", "").strip()

    if msg_type == "post":
        return _extract_post_text(content)

    return content_str


def _extract_post_text(content: dict) -> str:
    """Extract plain text from a rich-text (post) message."""
    lines = []
    post = content.get("post", content)
    for lang in ("zh_cn", "en_us", "ja_jp"):
        lang_post = post.get(lang)
        if lang_post:
            for para in lang_post.get("content", []):
                parts = []
                for elem in para:
                    tag = elem.get("tag", "")
                    if tag == "text":
                        parts.append(elem.get("text", ""))
                    elif tag == "a":
                        parts.append(elem.get("text", elem.get("href", "")))
                    elif tag == "at":
                        parts.append(f"@{elem.get('user_name', elem.get('user_id', ''))}")
                lines.append("".join(parts))
            return "\n".join(lines)
    return ""


def parse_command(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse a command from message text.
    Returns (command_name, argument) or (None, None) for normal messages.

    Supported commands:
        /mode <agent|ask|plan|debug>
        /model <model_name>
        /status
        /help
        /clear
        /context <file_path>
    """
    text = text.strip()
    if not text.startswith("/"):
        return None, None

    parts = text.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else None
    return cmd, arg


VALID_MODES = {"agent", "ask", "plan", "debug"}

MODE_DESCRIPTIONS = {
    "agent": "Agent 模式：自主执行，可读写文件、运行命令",
    "ask": "Ask 模式：只读分析，不做任何修改",
    "plan": "Plan 模式：先生成计划，确认后再执行",
    "debug": "Debug 模式：专注排查和修复问题",
}

VALID_MODELS = {"claude-4.6-opus", "gpt-5.4"}

DEFAULT_MODEL = "claude-4.6-opus"

HELP_TEXT = """可用指令：
/mode <agent|ask|plan|debug> — 切换交互模式
/model <模型名> — 切换 AI 模型
/new — 新建对话（清除上下文，保留模式和模型设置）
/status — 查看当前模式、模型、待处理消息数
/help — 显示本帮助
/clear — 完全重置（清除所有状态）
/context <文件路径> — 添加文件到对话上下文

可用模型：claude-4.6-opus | gpt-5.4

直接发送文本即可开始对话。"""


def format_reply_text(text: str) -> str:
    """Format a plain text reply for Feishu text message."""
    return json.dumps({"text": text}, ensure_ascii=False)


def format_reply_post(title: str, content_lines: list[str]) -> str:
    """
    Format a rich-text (post) reply for Feishu.
    Each content_line becomes a paragraph. Supports simple markdown-like formatting:
    - Lines starting with ``` enter/exit code blocks
    - **bold** for bold text
    - [text](url) for links
    """
    paragraphs = []
    in_code_block = False
    code_lines = []
    code_lang = ""

    for line in content_lines:
        if line.startswith("```"):
            if in_code_block:
                paragraphs.append([{
                    "tag": "code_block",
                    "language": code_lang or "plain_text",
                    "text": "\n".join(code_lines),
                }])
                code_lines = []
                code_lang = ""
                in_code_block = False
            else:
                in_code_block = True
                code_lang = line[3:].strip()
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        paragraphs.append(_parse_inline_elements(line))

    if code_lines:
        paragraphs.append([{
            "tag": "code_block",
            "language": code_lang or "plain_text",
            "text": "\n".join(code_lines),
        }])

    post = {
        "zh_cn": {
            "title": title,
            "content": paragraphs,
        }
    }
    return json.dumps(post, ensure_ascii=False)


def _parse_inline_elements(line: str) -> list[dict]:
    """Parse a line into Feishu rich-text inline elements."""
    elements = []
    remaining = line

    pattern = re.compile(
        r'(\*\*(.+?)\*\*)'       # **bold**
        r'|(\[(.+?)\]\((.+?)\))' # [text](url)
    )

    last_end = 0
    for m in pattern.finditer(remaining):
        if m.start() > last_end:
            elements.append({"tag": "text", "text": remaining[last_end:m.start()]})

        if m.group(2):
            elements.append({"tag": "text", "text": m.group(2), "style": ["bold"]})
        elif m.group(4):
            elements.append({"tag": "a", "text": m.group(4), "href": m.group(5)})

        last_end = m.end()

    if last_end < len(remaining):
        elements.append({"tag": "text", "text": remaining[last_end:]})

    if not elements:
        elements.append({"tag": "text", "text": ""})

    return elements


MAX_TEXT_LENGTH = 4000


def split_long_message(text: str) -> list[str]:
    """Split a long message into chunks that fit Feishu's size limit."""
    if len(text) <= MAX_TEXT_LENGTH:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > MAX_TEXT_LENGTH and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks
