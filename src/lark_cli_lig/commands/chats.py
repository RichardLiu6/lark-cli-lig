"""Chats commands: list bot's chats."""

from __future__ import annotations

import logging

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN

logger = logging.getLogger("lark_cli_lig.commands.chats")


def _make_api(ctx: click.Context) -> LarkAPI:
    identity = ctx.obj.get("identity", "bot")
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token(identity))


# ── chats ─────────────────────────────────────────────────────


@click.command()
@click.pass_context
def chats(ctx: click.Context) -> None:
    """List bot's chats."""
    api = _make_api(ctx)

    try:
        data = api.get("/open-apis/im/v1/chats", params={"page_size": "50"})
    except LarkAPIError as e:
        click.echo(f"❌ {e}")
        return

    items = data.get("items", [])
    if not items:
        click.echo("No chats found")
        return

    for c in items:
        name = c.get("name", "unnamed")
        chat_id = c.get("chat_id", "")
        chat_type = c.get("chat_type", "")
        click.echo(f"{name} | {chat_id} | {chat_type}")
