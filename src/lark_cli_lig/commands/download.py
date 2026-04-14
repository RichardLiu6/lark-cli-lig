"""Download files from Lark messages to local directory."""

from __future__ import annotations

import logging
from pathlib import Path

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN

logger = logging.getLogger("lark_cli_lig.commands.download")

DEFAULT_DIR = Path.home() / "Downloads"


def _make_api(ctx: click.Context) -> LarkAPI:
    identity = ctx.obj.get("identity", "bot")
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token(identity))


@click.command("download")
@click.argument("chat_id")
@click.option("-n", "--count", default=10, help="Number of recent messages to scan for files")
@click.option("-o", "--output", default=str(DEFAULT_DIR), help="Output directory (default: ~/Downloads)")
@click.option("--all", "download_all", is_flag=True, help="Download all files (default: only list them)")
@click.pass_context
def download(ctx: click.Context, chat_id: str, count: int, output: str, download_all: bool) -> None:
    """Download files from a Lark chat. Use 'my' for Richard's p2p chat.

    Without --all, lists available files. With --all, downloads them.

    \b
    Examples:
      lark download oc_5a5b... -n 20           # List recent files
      lark download oc_5a5b... -n 20 --all     # Download all files
      lark download oc_5a5b... --all -o ./      # Download to current dir
    """
    from ..commands.im import MY_CHAT_ID
    if chat_id == "my":
        chat_id = MY_CHAT_ID

    api = _make_api(ctx)
    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Get recent messages
    try:
        data = api.get(
            "/open-apis/im/v1/messages",
            params={
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": str(count),
                "sort_type": "ByCreateTimeDesc",
            },
        )
    except LarkAPIError as e:
        raise click.ClickException(str(e))

    items = data.get("items", [])
    import json
    from datetime import datetime

    files_found = []
    for m in items:
        msg_type = m.get("msg_type", "")
        msg_id = m.get("message_id", "")
        ts = m.get("create_time", "")
        dt = datetime.fromtimestamp(int(ts) / 1000).strftime("%m/%d %H:%M") if ts else "?"

        if msg_type == "file":
            try:
                content = json.loads(m.get("body", {}).get("content", "{}"))
                fname = content.get("file_name", "unknown")
                fkey = content.get("file_key", "")
                files_found.append({"name": fname, "key": fkey, "msg_id": msg_id, "date": dt})
            except (json.JSONDecodeError, KeyError):
                pass

    if not files_found:
        click.echo(f"No files found in last {count} messages.")
        return

    if not download_all:
        click.echo(f"Found {len(files_found)} file(s):")
        for i, f in enumerate(files_found, 1):
            click.echo(f"  {i}. [{f['date']}] {f['name']}")
        click.echo(f"\nUse --all to download, or -o to set output dir (default: ~/Downloads)")
        return

    # Download files
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    token = tm.get_token(ctx.obj.get("identity", "bot"))
    import httpx

    for f in files_found:
        url = f"https://open.larksuite.com/open-apis/im/v1/messages/{f['msg_id']}/resources/{f['key']}?type=file"
        click.echo(f"⬇ {f['name']}...", nl=False)
        resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if resp.status_code == 200 and len(resp.content) > 200:
            dest = out_dir / f["name"]
            dest.write_bytes(resp.content)
            click.echo(f" ✅ {len(resp.content) / 1024:.0f}KB → {dest}")
        else:
            click.echo(f" ❌ Failed ({resp.status_code}, {len(resp.content)}B)")

    click.echo(f"\nDone. Files saved to {out_dir}")
