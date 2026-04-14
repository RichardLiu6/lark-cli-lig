from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

def detect_id_type(target: str) -> str:
    """Detect Lark ID type from format: ou_ = open_id, oc_ = chat_id, else email"""
    if target.startswith("ou_"):
        return "open_id"
    elif target.startswith("oc_"):
        return "chat_id"
    return "email"

def format_timestamp(ts_ms: str | int) -> str:
    """Convert Lark timestamp (ms) to readable datetime string"""
    if not ts_ms or ts_ms == "0":
        return "—"
    ts = int(ts_ms) / 1000
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%Y-%m-%d %H:%M")

def to_rfc3339(date_str: str, time_str: str = "12:00:00", tz_offset: str = "-07:00") -> str:
    """Convert date string to RFC3339 format for Lark approval APIs.

    Examples:
        to_rfc3339("2026-03-24") → "2026-03-24T12:00:00-07:00"
        to_rfc3339("2026-03-24", "18:26:00") → "2026-03-24T18:26:00-07:00"
    """
    return f"{date_str}T{time_str}{tz_offset}"

def file_type_from_ext(filename: str) -> str:
    """Map file extension to Lark file type for upload"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    types = {
        "pdf": "pdf", "doc": "doc", "docx": "doc",
        "xls": "xls", "xlsx": "xls",
        "ppt": "ppt", "pptx": "ppt",
        "mp4": "mp4", "mp3": "mp3",
        "zip": "stream", "txt": "stream", "csv": "stream",
    }
    return types.get(ext, "stream")

def truncate(text: str, max_len: int = 80) -> str:
    """Truncate text for display"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
