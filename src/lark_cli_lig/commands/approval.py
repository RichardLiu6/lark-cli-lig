"""Approval workflow commands: list types, query instances, get details, upload, submit."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN
from ..utils import format_timestamp


def _make_api(ctx: click.Context) -> LarkAPI:
    """Create a LarkAPI for approval commands — always uses tenant (bot) token.

    Approval API endpoints require tenant_access_token by design.
    User identity is conveyed via open_id in the request payload, not via token.
    """
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token("bot"))


def _make_api_for_identity(identity: str) -> LarkAPI:
    """Create a LarkAPI with a specific identity (bot or user)."""
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token(identity))


@click.group()
@click.pass_context
def approval(ctx: click.Context) -> None:
    """Approval workflows (reimbursement, leave, procurement)."""
    pass


@approval.command("types")
@click.pass_context
def approval_types(ctx: click.Context) -> None:
    """List approval definitions."""
    api = _make_api(ctx)

    try:
        data = api.get("/open-apis/approval/v4/approvals", params={"page_size": "50"})
    except LarkAPIError as e:
        # If tenant token fails with 99991663, try user token
        if e.code == 99991663:
            click.echo("Tenant token failed, trying user token...", err=True)
            api = _make_api_for_identity("user")
            try:
                data = api.get("/open-apis/approval/v4/approvals", params={"page_size": "50"})
            except LarkAPIError as e2:
                raise click.ClickException(str(e2))
        else:
            raise click.ClickException(str(e))

    items = data.get("approval_list", [])
    if not items:
        click.echo("No approval definitions found")
        return

    click.echo(f"{'Approval Name':40s} | {'Code':30s} | {'Group'}")
    click.echo("-" * 90)
    for a in items:
        name = a.get("approval_name", "?")
        code = a.get("approval_code", "?")
        group = a.get("group_name", "\u2014")
        click.echo(f"{name:40s} | {code:30s} | {group}")


@approval.command("my")
@click.option("-n", "--count", default=20, help="Number of results (min 5 per Lark API)")
@click.option("--type", "approval_type", default=None,
              help="Filter by type: purchase/reimbursement/overtime/leave/payment")
@click.option("--status", "status_filter", default=None,
              help="Filter by status: pending/approved/rejected/canceled")
@click.pass_context
def approval_my(ctx: click.Context, count: int, approval_type: str | None, status_filter: str | None) -> None:
    """List my submitted approvals."""
    # Map type shortcuts to approval codes
    type_map = {
        "purchase": "1C6CDDFA-3222-4D4E-A2E3-1BB2C6B5CA84",
        "reimbursement": "89618889-E377-43E4-86E3-A421CFB4B845",
        "overtime": "8FF508FD-EC2B-4625-90C0-5C695DA80084",
        "leave": "4EFC010E-2264-49EC-ACB4-5B520828F3C7",
        "payment": "71CC45C9-955B-4CF3-9D4A-A65E8E66E2BA",
    }

    api = _make_api(ctx)

    # Lark API requires page_size >= 5
    api_page_size = max(count, 5)

    # Build query body — approval_code + time range are required
    body: dict = {
        "start_time": "1700000000000",  # ~2023-11
        "end_time": "1800000000000",    # ~2027-01
    }

    if approval_type:
        code = type_map.get(approval_type.lower())
        if not code:
            raise click.ClickException(
                f"Unknown type '{approval_type}'. Use: {', '.join(type_map.keys())}"
            )
        body["approval_code"] = code
    else:
        # Without approval_code, query all known types and merge
        all_instances = []
        for atype, acode in type_map.items():
            try:
                data = api.post(
                    f"/open-apis/approval/v4/instances/query?page_size={api_page_size}",
                    body={**body, "approval_code": acode},
                )
                for inst in data.get("instance_list", []):
                    all_instances.append(inst)
            except LarkAPIError:
                continue
        # Sort by start_time desc
        all_instances.sort(
            key=lambda x: x.get("instance", {}).get("start_time", "0"),
            reverse=True,
        )
        items = all_instances[:count]
        _print_instance_list(items, status_filter)
        return

    try:
        data = api.post(
            f"/open-apis/approval/v4/instances/query?page_size={api_page_size}",
            body=body,
        )
    except LarkAPIError as e:
        raise click.ClickException(str(e))

    items = data.get("instance_list", [])
    _print_instance_list(items, status_filter)


def _print_instance_list(items: list, status_filter: str | None = None) -> None:
    """Print approval instances in a table format."""
    if not items:
        click.echo("No approval instances found")
        return

    # Filter by status if requested
    if status_filter:
        items = [
            i for i in items
            if i.get("instance", {}).get("status", "").lower() == status_filter.lower()
        ]

    if not items:
        click.echo(f"No instances with status '{status_filter}'")
        return

    click.echo(f"{'Instance Code':40s} | {'Approval Name':25s} | {'Status':12s} | {'Start Time'}")
    click.echo("-" * 100)
    for i in items:
        # Parse nested structure: instance.code, approval.name, instance.status
        inst = i.get("instance", {})
        appr = i.get("approval", {})
        code = inst.get("code", i.get("instance_code", "?"))
        name = appr.get("name", i.get("approval_name", "\u2014"))
        status = inst.get("status", i.get("status", "?"))
        dt = format_timestamp(inst.get("start_time", i.get("start_time", "")))
        click.echo(f"{code:40s} | {name:25s} | {status:12s} | {dt}")


@approval.command("get")
@click.argument("instance_code")
@click.pass_context
def approval_get(ctx: click.Context, instance_code: str) -> None:
    """Get approval instance details."""
    api = _make_api(ctx)

    try:
        inst = api.get(f"/open-apis/approval/v4/instances/{instance_code}")
    except LarkAPIError as e:
        raise click.ClickException(str(e))

    # Basic info
    click.echo(f"Approval: {inst.get('approval_name', '?')}")
    click.echo(f"Status:   {inst.get('status', '?')}")
    click.echo(f"Started:  {format_timestamp(inst.get('start_time', ''))}")
    click.echo()

    # Form data
    form_raw = inst.get("form", "[]")
    try:
        form_data = json.loads(form_raw) if isinstance(form_raw, str) else form_raw
        click.echo("\u2500\u2500 Form Data \u2500\u2500")
        for f in form_data:
            name = f.get("name", "?")
            value = f.get("value", "")
            click.echo(f"  {name}: {value}")
    except (json.JSONDecodeError, TypeError):
        click.echo(f"Form (raw): {form_raw}")
    click.echo()

    # Task list
    tasks = inst.get("task_list", [])
    if tasks:
        click.echo("\u2500\u2500 Tasks \u2500\u2500")
        for t in tasks:
            user = t.get("open_id", "?")
            status = t.get("status", "?")
            node = t.get("node_name", "")
            click.echo(f"  {node:20s} | {user:30s} | {status}")
    click.echo()

    # Timeline
    timeline = inst.get("timeline", [])
    if timeline:
        click.echo("\u2500\u2500 Timeline \u2500\u2500")
        for e in timeline:
            edt = format_timestamp(e.get("create_time", ""))
            etype = e.get("type", "?")
            user = e.get("open_id", "")
            comment = e.get("comment", "")
            line = f"  [{edt}] {etype}"
            if user:
                line += f" by {user}"
            if comment:
                line += f" \u2014 {comment}"
            click.echo(line)


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic"}


@approval.command("upload")
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "--type",
    "upload_type",
    type=click.Choice(["image", "attachment"]),
    default=None,
    help=(
        "Target field type. 'image' (for image widgets) auto-converts PDF→PNG "
        "but only keeps page 1 — use pdf_split.py first for multi-page. "
        "'attachment' (for attachmentV2 widgets) keeps original format. "
        "Default: auto-detect by extension (image for jpg/png/etc, attachment for others)."
    ),
)
@click.pass_context
def approval_upload(ctx: click.Context, file: str, upload_type: str | None) -> None:
    """Upload a file for approval. Auto-detects image vs attachment by extension."""
    filepath = Path(file)
    filename = filepath.name
    ext = filepath.suffix.lower()

    # Auto-detect target field type if not specified
    if upload_type is None:
        upload_type = "image" if ext in _IMAGE_EXTS else "attachment"

    # Only convert PDF→PNG when target is an image field (image widget needs page-1 PNG)
    if ext == ".pdf" and upload_type == "image":
        try:
            from pdf2image import convert_from_path
        except ImportError:
            raise click.ClickException(
                "pdf2image is required for PDF conversion. Install with: pip install pdf2image"
            )
        click.echo(
            "Warning: uploading PDF to image field — only page 1 will render. "
            "Use scripts/pdf_split.py first if you need all pages."
        )
        imgs = convert_from_path(str(filepath), dpi=200)
        tmp_path = Path("/tmp/lark-approval-upload.png")
        imgs[0].save(str(tmp_path), "PNG")
        filepath = tmp_path
        filename = "converted.png"

    api = _make_api(ctx)

    try:
        with open(filepath, "rb") as f:
            data = api.upload(
                "/open-apis/approval/v4/files/upload",
                files={"content": (filename, f, "application/octet-stream")},
                data={"name": filename, "type": upload_type},
            )
    except LarkAPIError as e:
        raise click.ClickException(str(e))

    code = data.get("code", "")
    if code:
        click.echo(f"Uploaded. File code: {code}")
    else:
        click.echo("Upload succeeded but no file code returned")
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))


@approval.command("submit")
@click.argument("json_input", default="-")
@click.pass_context
def approval_submit(ctx: click.Context, json_input: str) -> None:
    """Submit an approval instance (from JSON file or stdin)."""
    # Read JSON input
    if json_input == "-":
        raw = sys.stdin.read()
    else:
        p = Path(json_input)
        if not p.exists():
            raise click.ClickException(f"File not found: {json_input}")
        raw = p.read_text()

    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON: {e}")

    approval_code = input_data.get("approval_code", "")
    if not approval_code:
        raise click.ClickException("Missing 'approval_code' in JSON")

    form = input_data.get("form", [])
    # Get open_id: from JSON input, or from logged-in user
    open_id = input_data.get("open_id", "")
    if not open_id:
        tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
        open_id = tm.get_current_open_id()

    payload = {
        "approval_code": approval_code,
        "open_id": open_id,
        "form": json.dumps(form) if isinstance(form, list) else form,
    }

    api = _make_api(ctx)

    try:
        data = api.post("/open-apis/approval/v4/instances", body=payload)
    except LarkAPIError as e:
        raise click.ClickException(str(e))

    instance_code = data.get("instance_code", "")
    if instance_code:
        click.echo(f"Submitted. Instance code: {instance_code}")
    else:
        click.echo("Submit succeeded but no instance_code returned")
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
