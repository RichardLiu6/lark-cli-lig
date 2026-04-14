"""Authentication CLI commands: login, status, logout."""

from __future__ import annotations

import time
from datetime import datetime

import click

from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN


@click.group()
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Manage authentication."""
    pass


@auth.command()
@click.option("--scope", multiple=True, help="OAuth scopes to request (can be repeated)")
@click.pass_context
def login(ctx: click.Context, scope: tuple[str, ...]) -> None:
    """Login via OAuth (opens browser)."""
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    scopes = list(scope) if scope else None
    result = tm.oauth_login(scopes)
    name = result.get("name", "user")
    click.echo(f"Logged in as {name}")
    if result.get("open_id"):
        click.echo(f"  open_id: {result['open_id']}")
    if result.get("expires_in"):
        click.echo(f"  token expires in: {result['expires_in']}s")


@auth.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show current auth status."""
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    s = tm.status()

    click.echo(f"App ID:        {s.get('app_id', '?')}")
    click.echo(f"Domain:        {s.get('domain', '?')}")
    click.echo()

    # Tenant token
    tenant_valid = s.get("tenant_token_valid", False)
    tenant_icon = "valid" if tenant_valid else "expired/missing"
    click.echo(f"Tenant token:  {tenant_icon}")
    if s.get("tenant_token_expires_at"):
        exp = datetime.fromtimestamp(s["tenant_token_expires_at"]).strftime("%Y-%m-%d %H:%M:%S")
        click.echo(f"  expires at:  {exp}")

    # User token
    user_logged_in = s.get("user_logged_in", False)
    if user_logged_in:
        active = s.get("user_token_active", False)
        user_icon = "valid" if active else "expired (refresh available)"
        click.echo(f"User token:    {user_icon}")
        if s.get("user_name"):
            click.echo(f"  User:        {s['user_name']}")
        if s.get("user_open_id"):
            click.echo(f"  open_id:     {s['user_open_id']}")
        if s.get("user_token_expires_at"):
            exp = datetime.fromtimestamp(s["user_token_expires_at"]).strftime("%Y-%m-%d %H:%M:%S")
            click.echo(f"  expires at:  {exp}")
    else:
        click.echo("User token:    not logged in")


@auth.command()
def logout() -> None:
    """Remove stored tokens."""
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    tm.logout()
    click.echo("Logged out")
