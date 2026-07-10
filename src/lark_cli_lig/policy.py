"""Client-side capability policy (accident-prevention layer).

⚠️  NOT A SECURITY BOUNDARY.  While APP_SECRET is present on the machine, any
restriction here is bypassable by editing config or calling the Lark API
directly with a self-minted token. This layer exists to prevent honest
mistakes and casual misuse by non-admin users. Real, non-bypassable access
control requires the central gateway (secret removed from all clients) —
see docs/permission-rollout-plan.md.

Role model (Phase 1, two tiers):
  admin   — full, unrestricted (Richard / a controlled ops account). Default.
  member  — any non-"admin" role. Restricted:
              • cannot use `--as bot` (tenant token)
              • cannot use the raw `api` passthrough command
              • `approval submit` is forced to the caller's own open_id
"""

from __future__ import annotations

import click

from .config import ROLE

ADMIN = "admin"


def current_role() -> str:
    return ROLE


def is_admin() -> bool:
    return ROLE == ADMIN


def _deny(capability: str) -> None:
    raise click.ClickException(
        f"'{capability}' is disabled for role '{ROLE}'. "
        f"This capability is restricted to the 'admin' role. "
        f"If you need it, ask Richard (an admin) to run it."
    )


def require_bot_identity() -> None:
    """Block `--as bot` for non-admin roles."""
    if not is_admin():
        _deny("--as bot")


def require_raw_api() -> None:
    """Block the raw `api` passthrough for non-admin roles."""
    if not is_admin():
        _deny("api")


def allows_foreign_open_id() -> bool:
    """Whether the caller may submit approvals on behalf of another open_id."""
    return is_admin()
