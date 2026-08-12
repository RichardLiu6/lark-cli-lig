"""Approval workflow commands: list types, query instances, get details, upload, submit."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN
from ..utils import format_timestamp


# Per-type query in `approval my`: one retry absorbs transient server errors
# (e.g. 1395001 "internal error") instead of silently dropping a whole type.
_QUERY_ATTEMPTS = 2
_QUERY_RETRY_DELAY = 0.5

# Marker prefix for a partially-loaded list. Deliberately contains no
# UUID-shaped text so callers grepping instance codes stay unaffected.
INCOMPLETE_MARKER = "!! INCOMPLETE LIST"

APPROVAL_TYPE_CODES = {
    "purchase": "1C6CDDFA-3222-4D4E-A2E3-1BB2C6B5CA84",
    "reimbursement": "89618889-E377-43E4-86E3-A421CFB4B845",
    "overtime": "8FF508FD-EC2B-4625-90C0-5C695DA80084",
    "leave": "4EFC010E-2264-49EC-ACB4-5B520828F3C7",
    "payment": "71CC45C9-955B-4CF3-9D4A-A65E8E66E2BA",
}

_INSTANCE_CODE_RE = re.compile(
    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$",
    re.IGNORECASE,
)
_PROCESS_NUMBER_RE = re.compile(r"^\d{12}$")


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
    type_map = APPROVAL_TYPE_CODES

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
        # Without approval_code, query all known types and merge.
        # A per-type failure must NEVER be swallowed: the merged list is then
        # silently short by a whole type, and later entries slide into the
        # top-N window. Callers that diff this list (approval-submit-verify
        # hook) would read that as "old instances appeared / new one absent".
        # Seen 2026-07-31: payment type returned 1395001 "internal error".
        all_instances = []
        failed_types: list[tuple[str, str]] = []
        for atype, acode in type_map.items():
            last_err: LarkAPIError | None = None
            for attempt in range(_QUERY_ATTEMPTS):
                try:
                    data = api.post(
                        f"/open-apis/approval/v4/instances/query?page_size={api_page_size}",
                        body={**body, "approval_code": acode},
                    )
                    for inst in data.get("instance_list", []):
                        all_instances.append(inst)
                    last_err = None
                    break
                except LarkAPIError as e:
                    # 1395001 & friends are transient server-side hiccups;
                    # one retry recovers them without a visible gap.
                    last_err = e
                    if attempt < _QUERY_ATTEMPTS - 1:
                        time.sleep(_QUERY_RETRY_DELAY)
            if last_err is not None:
                failed_types.append((atype, str(last_err.code)))
        # Sort by start_time desc
        all_instances.sort(
            key=lambda x: x.get("instance", {}).get("start_time", "0"),
            reverse=True,
        )
        items = all_instances[:count]
        _print_instance_list(items, status_filter)
        _warn_incomplete(failed_types)
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


def _warn_incomplete(failed_types: list[tuple[str, str]]) -> None:
    """Announce a partially-loaded list on BOTH stdout and stderr.

    stdout carries it too because scripted callers commonly run this command
    with `2>/dev/null`; a truncated list that looks complete is the dangerous
    case, so the warning has to travel on the stream they actually read.
    """
    if not failed_types:
        return
    detail = ", ".join(f"{atype} (error {code})" for atype, code in failed_types)
    line = (
        f"{INCOMPLETE_MARKER} — {len(failed_types)} approval type(s) failed to load "
        f"after {_QUERY_ATTEMPTS} attempts: {detail}. "
        "The list above is missing those types — do not treat it as complete."
    )
    click.echo(line)
    click.echo(line, err=True)


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


def _resolve_instance_reference(api: LarkAPI, reference: str) -> tuple[str, str]:
    """Resolve a user-facing process number or an internal instance code.

    Lark does not expose a direct lookup endpoint for process numbers. Its
    instance-query response does include ``serial_id``, so scan the known ABL
    approval definitions and keep the UUID private from normal CLI output.
    """
    reference = reference.strip()
    if _INSTANCE_CODE_RE.fullmatch(reference):
        inst = api.get(f"/open-apis/approval/v4/instances/{reference}")
        return reference, str(inst.get("serial_number", ""))

    if not _PROCESS_NUMBER_RE.fullmatch(reference):
        raise click.ClickException(
            "Approval reference must be a 12-digit process number "
            "(for example 202608120002) or an instance code."
        )

    failed_types: list[str] = []
    query_body = {
        # The current Lark query endpoint is reliable for this tenant only with
        # the same broad range used by `approval my`.
        "start_time": "1700000000000",
        "end_time": "1800000000000",
    }
    for approval_type, approval_code in APPROVAL_TYPE_CODES.items():
        try:
            data = api.post(
                "/open-apis/approval/v4/instances/query?page_size=100",
                body={**query_body, "approval_code": approval_code},
            )
        except LarkAPIError:
            failed_types.append(approval_type)
            continue

        for item in data.get("instance_list", []):
            inst = item.get("instance", {})
            serial = str(inst.get("serial_id", inst.get("serial_number", "")))
            if serial == reference:
                code = str(inst.get("code", item.get("instance_code", "")))
                if code:
                    return code, serial

    suffix = ""
    if failed_types:
        suffix = f" ({', '.join(failed_types)} could not be searched)"
    raise click.ClickException(
        f"Process number {reference} was not found in known approval types{suffix}."
    )


def _comment_params() -> dict[str, str]:
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return {
        "user_id": tm.get_current_open_id(),
        "user_id_type": "open_id",
    }


def _decode_comment_text(content: object) -> str:
    if isinstance(content, dict):
        return str(content.get("text", ""))
    if not isinstance(content, str):
        return str(content)
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(decoded, dict):
        return str(decoded.get("text", content))
    return content


@approval.command("comment")
@click.argument("approval_ref")
@click.argument("text")
@click.option(
    "--verify/--no-verify",
    default=True,
    help="Read comments back after creation to verify the new comment (default: verify).",
)
@click.pass_context
def approval_comment(
    ctx: click.Context,
    approval_ref: str,
    text: str,
    verify: bool,
) -> None:
    """Add a comment using a searchable process number or instance code."""
    api = _make_api(ctx)
    try:
        instance_code, process_number = _resolve_instance_reference(api, approval_ref)
        params = _comment_params()
        path = f"/open-apis/approval/v4/instances/{instance_code}/comments"
        data = api.post(
            path,
            params=params,
            body={
                "content": json.dumps(
                    {"text": text, "files": []},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "disable_bot": False,
            },
        )
        comment_id = str(data.get("comment_id", ""))

        if verify:
            comments = api.get(path, params={**params, "page_size": "50"}).get(
                "comments", []
            )
            if not any(str(item.get("id", "")) == comment_id for item in comments):
                raise click.ClickException(
                    "Comment creation returned success, but read-back verification failed."
                )
    except LarkAPIError as e:
        raise click.ClickException(str(e))

    label = process_number or approval_ref
    suffix = " and verified" if verify else ""
    click.echo(f"Process {label}: comment added{suffix}.")


@approval.command("comments")
@click.argument("approval_ref")
@click.pass_context
def approval_comments(ctx: click.Context, approval_ref: str) -> None:
    """List comments using a searchable process number or instance code."""
    api = _make_api(ctx)
    try:
        instance_code, process_number = _resolve_instance_reference(api, approval_ref)
        params = {**_comment_params(), "page_size": "50"}
        data = api.get(
            f"/open-apis/approval/v4/instances/{instance_code}/comments",
            params=params,
        )
    except LarkAPIError as e:
        raise click.ClickException(str(e))

    label = process_number or approval_ref
    comments = [c for c in data.get("comments", []) if not c.get("is_delete")]
    click.echo(f"Comments for process {label}")
    if not comments:
        click.echo("No comments found")
        return

    for item in comments:
        created = format_timestamp(item.get("create_time", ""))
        click.echo(f"[{created}] {_decode_comment_text(item.get('content', ''))}")


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
    # Get open_id: from JSON input, or from logged-in user.
    open_id = input_data.get("open_id", "")

    # Anti-impersonation: non-admin roles may only submit as themselves. If the
    # payload names someone else, override it with the caller's own open_id and
    # warn. (Approval identity is a payload field, not an auth token — without
    # this, anyone could file approvals in a colleague's name.)
    from ..policy import allows_foreign_open_id
    if not allows_foreign_open_id():
        tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
        own = tm.get_current_open_id()
        if open_id and open_id != own:
            click.echo(
                f"Warning: role not allowed to submit on behalf of {open_id}; "
                f"submitting as yourself ({own}).",
                err=True,
            )
        open_id = own
    elif not open_id:
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
