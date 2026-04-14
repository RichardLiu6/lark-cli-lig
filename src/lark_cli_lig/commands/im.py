"""IM (messaging) commands: send, send-image, send-file, send-group, read."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN
from ..utils import detect_id_type, file_type_from_ext

logger = logging.getLogger("lark_cli_lig.commands.im")

MY_CHAT_ID = "oc_e6fe8d9bdc3dbe434749ae7ac5899820"


def _make_api(ctx: click.Context) -> LarkAPI:
    identity = ctx.obj.get("identity", "bot")
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token(identity))


# ── send ──────────────────────────────────────────────────────


@click.command()
@click.argument("target")
@click.argument("message", nargs=-1, required=True)
@click.pass_context
def send(ctx: click.Context, target: str, message: tuple[str, ...]) -> None:
    """Send a text message to a user (email or open_id)."""
    api = _make_api(ctx)
    id_type = detect_id_type(target)
    text = " ".join(message)

    payload = {
        "receive_id": target,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }

    try:
        api.post(f"/open-apis/im/v1/messages?receive_id_type={id_type}", body=payload)
        click.echo(f"✅ Sent to {target}")
    except LarkAPIError as e:
        click.echo(f"❌ {e}")


# ── send-image ────────────────────────────────────────────────


@click.command("send-image")
@click.argument("target")
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
def send_image(ctx: click.Context, target: str, file: str) -> None:
    """Send an image to a user (png/jpg)."""
    api = _make_api(ctx)
    id_type = detect_id_type(target)
    filepath = Path(file)

    # Upload image
    try:
        with open(filepath, "rb") as f:
            upload_data = api.upload(
                "/open-apis/im/v1/images",
                files={"image": (filepath.name, f, "application/octet-stream")},
                data={"image_type": "message"},
            )
        image_key = upload_data.get("image_key", "")
        if not image_key:
            click.echo("❌ Image upload succeeded but no image_key returned")
            return
        logger.info("Uploaded image: %s", image_key)
    except LarkAPIError as e:
        click.echo(f"❌ Image upload failed: {e}")
        return

    # Send image message
    payload = {
        "receive_id": target,
        "msg_type": "image",
        "content": json.dumps({"image_key": image_key}),
    }

    try:
        api.post(f"/open-apis/im/v1/messages?receive_id_type={id_type}", body=payload)
        click.echo(f"✅ Image sent to {target}")
    except LarkAPIError as e:
        click.echo(f"❌ {e}")


# ── send-file ─────────────────────────────────────────────────


@click.command("send-file")
@click.argument("target")
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
def send_file(ctx: click.Context, target: str, file: str) -> None:
    """Send a file to a user (pdf/doc/xls...)."""
    api = _make_api(ctx)
    id_type = detect_id_type(target)
    filepath = Path(file)
    filename = filepath.name
    ftype = file_type_from_ext(filename)

    # Upload file
    try:
        with open(filepath, "rb") as f:
            upload_data = api.upload(
                "/open-apis/im/v1/files",
                files={"file": (filename, f, "application/octet-stream")},
                data={"file_type": ftype, "file_name": filename},
            )
        file_key = upload_data.get("file_key", "")
        if not file_key:
            click.echo("❌ File upload succeeded but no file_key returned")
            return
        logger.info("Uploaded file: %s (type=%s)", file_key, ftype)
    except LarkAPIError as e:
        click.echo(f"❌ File upload failed: {e}")
        return

    # Send file message
    payload = {
        "receive_id": target,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }

    try:
        api.post(f"/open-apis/im/v1/messages?receive_id_type={id_type}", body=payload)
        click.echo(f'✅ File "{filename}" sent to {target}')
    except LarkAPIError as e:
        click.echo(f"❌ {e}")


# ── send-group ────────────────────────────────────────────────


@click.command("send-group")
@click.argument("chat_id")
@click.argument("message", nargs=-1, required=True)
@click.pass_context
def send_group(ctx: click.Context, chat_id: str, message: tuple[str, ...]) -> None:
    """Send a text message to a group chat."""
    api = _make_api(ctx)
    text = " ".join(message)

    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }

    try:
        api.post("/open-apis/im/v1/messages?receive_id_type=chat_id", body=payload)
        click.echo("✅ Sent to group")
    except LarkAPIError as e:
        click.echo(f"❌ {e}")


# ── read ──────────────────────────────────────────────────────


@click.command()
@click.argument("chat_id")
@click.option("-n", "--count", default=10, help="Number of messages to read")
@click.pass_context
def read(ctx: click.Context, chat_id: str, count: int) -> None:
    """Read messages from a chat ('my' = Richard's p2p chat)."""
    api = _make_api(ctx)

    if chat_id == "my":
        chat_id = MY_CHAT_ID

    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "page_size": str(count),
        "sort_type": "ByCreateTimeDesc",
    }

    try:
        data = api.get("/open-apis/im/v1/messages", params=params)
    except LarkAPIError as e:
        click.echo(f"❌ {e}")
        return

    items = data.get("items", [])
    if not items:
        click.echo("No messages found")
        return

    # Reverse so oldest is first (API returns newest first)
    for m in reversed(items):
        sender = m.get("sender", {})
        sender_type = sender.get("sender_type", "?")
        msg_type = m.get("msg_type", "text")

        try:
            content = json.loads(m.get("body", {}).get("content", "{}"))
            if msg_type == "text":
                text = content.get("text", "")
            elif msg_type == "image":
                text = "[image: " + content.get("image_key", "?") + "]"
            elif msg_type == "file":
                text = "[file: " + content.get("file_name", content.get("file_key", "?")) + "]"
            elif msg_type == "audio":
                text = "[audio]"
            elif msg_type == "post":
                post_content = content.get("zh_cn", content.get("en_us", {}))
                title = post_content.get("title", "") if isinstance(post_content, dict) else ""
                text = f"[post: {title}]" if title else "[post]"
            else:
                text = f"[{msg_type}]"
        except Exception:
            text = f"[{msg_type}]"

        ts = m.get("create_time", "")
        if ts:
            try:
                dt = datetime.fromtimestamp(int(ts) / 1000).strftime("%m/%d %H:%M")
            except (ValueError, OSError):
                dt = "?"
        else:
            dt = "?"

        who = "🤖" if sender_type == "app" else "👤"
        click.echo(f"{who} [{dt}] {text}")
