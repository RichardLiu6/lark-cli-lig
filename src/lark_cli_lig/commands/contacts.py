"""Contacts commands: users, users-all."""

from __future__ import annotations

import logging
from typing import Iterator

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN
from .approval import INCOMPLETE_MARKER

logger = logging.getLogger("lark_cli_lig.commands.contacts")

_PAGE_SIZE = "50"


def _make_api(ctx: click.Context) -> LarkAPI:
    identity = ctx.obj.get("identity", "bot")
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token(identity))


def _paginate(api: LarkAPI, path: str, params: dict[str, str]) -> Iterator[dict]:
    """Yield every item of a Lark list endpoint, following page_token/has_more."""
    params = dict(params)
    while True:
        data = api.get(path, params=params)
        yield from data.get("items") or []
        token = data.get("page_token")
        if not data.get("has_more") or not token:
            return
        params["page_token"] = token


def _warn_incomplete(failed: list[tuple[str, str]]) -> None:
    """Same contract as `approval my`: marker on stdout AND stderr."""
    if not failed:
        return
    detail = ", ".join(f"{name} ({err})" for name, err in failed)
    line = (
        f"{INCOMPLETE_MARKER} — {len(failed)} department(s) failed to load: {detail}. "
        "Users in those departments are missing — do not treat this list as complete."
    )
    click.echo(line)
    click.echo(line, err=True)


# ── users (root department) ───────────────────────────────────


@click.command()
@click.pass_context
def users(ctx: click.Context) -> None:
    """List root department users (direct members only; see users-all)."""
    api = _make_api(ctx)

    try:
        items = list(_paginate(
            api,
            "/open-apis/contact/v3/users",
            {"department_id": "0", "page_size": _PAGE_SIZE, "user_id_type": "open_id"},
        ))
    except LarkAPIError as e:
        click.echo(f"❌ {e}", err=True)
        ctx.exit(1)

    click.echo(f"Found {len(items)} users:")
    for u in items:
        name = u.get("name", "?")
        email = u.get("email", "—")
        open_id = u.get("open_id", "")
        click.echo(f"  {name:20s} | {email:35s} | {open_id}")


# ── users-all (all departments) ──────────────────────────────


@click.command("users-all")
@click.pass_context
def users_all(ctx: click.Context) -> None:
    """List ALL org users across every department level."""
    api = _make_api(ctx)

    # fetch_child=true returns descendants at every depth, not just level 1
    # (Production/Quality/… live two levels down since the 2026-10 reorg).
    try:
        depts = list(_paginate(
            api,
            "/open-apis/contact/v3/departments/0/children",
            {
                "page_size": _PAGE_SIZE,
                "fetch_child": "true",
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
            },
        ))
    except LarkAPIError as e:
        click.echo(f"❌ Failed to list departments: {e}", err=True)
        ctx.exit(1)

    all_depts: list[tuple[str, str]] = [("0", "Root")]
    for d in depts:
        all_depts.append((d.get("open_department_id", ""), d.get("name", "?")))

    seen_open_ids: set[str] = set()
    failed: list[tuple[str, str]] = []

    for dept_id, dept_name in all_depts:
        params = {"page_size": _PAGE_SIZE, "user_id_type": "open_id", "department_id": dept_id}
        if dept_id != "0":
            params["department_id_type"] = "open_department_id"

        try:
            user_items = list(_paginate(api, "/open-apis/contact/v3/users", params))
        except LarkAPIError as e:
            click.echo(f"❌ Failed to list users for dept {dept_name} ({dept_id}): {e}", err=True)
            failed.append((dept_name, f"error {e.code}"))
            continue

        for u in user_items:
            oid = u.get("open_id", "")
            if oid in seen_open_ids:
                continue
            seen_open_ids.add(oid)

            name = u.get("name", "?")
            email = u.get("email", "—")
            click.echo(f"{dept_name:25s} | {name:20s} | {email:35s} | {oid}")

    click.echo(
        f"Total: {len(seen_open_ids)} users across {len(all_depts)} departments "
        f"({len(failed)} failed)",
        err=True,
    )
    _warn_incomplete(failed)
