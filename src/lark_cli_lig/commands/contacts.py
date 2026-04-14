"""Contacts commands: users, users-all."""

from __future__ import annotations

import logging

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN

logger = logging.getLogger("lark_cli_lig.commands.contacts")


def _make_api(ctx: click.Context) -> LarkAPI:
    identity = ctx.obj.get("identity", "bot")
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token(identity))


# ── users (root department) ───────────────────────────────────


@click.command()
@click.pass_context
def users(ctx: click.Context) -> None:
    """List root department users."""
    api = _make_api(ctx)

    try:
        data = api.get(
            "/open-apis/contact/v3/users",
            params={
                "department_id": "0",
                "page_size": "50",
                "user_id_type": "open_id",
            },
        )
    except LarkAPIError as e:
        click.echo(f"❌ {e}")
        return

    items = data.get("items", [])
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
    """List ALL org users across all departments."""
    api = _make_api(ctx)

    # Get child departments of root
    try:
        depts_data = api.get(
            "/open-apis/contact/v3/departments/0/children",
            params={"page_size": "50"},
        )
    except LarkAPIError as e:
        click.echo(f"❌ Failed to list departments: {e}")
        return

    depts = depts_data.get("items", [])

    # Build list: root + all child departments
    all_depts: list[tuple[str, str, bool]] = [("0", "Root", True)]
    for d in depts:
        dept_id = d.get("open_department_id", "")
        dept_name = d.get("name", "?")
        all_depts.append((dept_id, dept_name, False))

    seen_open_ids: set[str] = set()

    for dept_id, dept_name, is_root in all_depts:
        params: dict[str, str] = {
            "page_size": "50",
            "user_id_type": "open_id",
        }
        if is_root:
            params["department_id"] = "0"
        else:
            params["department_id"] = dept_id
            params["department_id_type"] = "open_department_id"

        try:
            users_data = api.get("/open-apis/contact/v3/users", params=params)
        except LarkAPIError as e:
            logger.warning("Failed to list users for dept %s: %s", dept_name, e)
            continue

        user_items = users_data.get("items", [])
        for u in user_items:
            oid = u.get("open_id", "")
            if oid in seen_open_ids:
                continue
            seen_open_ids.add(oid)

            name = u.get("name", "?")
            email = u.get("email", "—")
            click.echo(f"{dept_name:25s} | {name:20s} | {email:35s} | {oid}")
