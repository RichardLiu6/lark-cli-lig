"""Raw API command: call any Lark Open API endpoint directly."""

from __future__ import annotations

import json

import click

from ..api import LarkAPI, LarkAPIError
from ..auth import TokenManager
from ..config import APP_ID, APP_SECRET, DOMAIN


def _make_api(ctx: click.Context, identity: str | None = None) -> LarkAPI:
    """Create a LarkAPI instance using the given or context identity."""
    identity = identity or ctx.obj.get("identity", "bot")
    tm = TokenManager(APP_ID, APP_SECRET, DOMAIN)
    return LarkAPI(DOMAIN, lambda: tm.get_token(identity))


@click.command("api")
@click.argument("method", type=click.Choice(["GET", "POST", "PUT", "DELETE", "PATCH"], case_sensitive=False))
@click.argument("path")
@click.option("-d", "--body", default=None, help="JSON request body")
@click.option("-p", "--params", default=None, help="Query params as JSON string")
@click.pass_context
def api_cmd(ctx: click.Context, method: str, path: str, body: str | None, params: str | None) -> None:
    """Call any Lark Open API endpoint directly.

    \b
    Examples:
      lark-lig api GET /open-apis/approval/v4/approvals
      lark-lig api POST /open-apis/im/v1/messages --body '{"receive_id":"ou_xxx",...}'
      lark-lig api GET /open-apis/contact/v3/users --params '{"department_id":"0","page_size":"3"}'
    """
    # Raw API passthrough bypasses every command-level restriction, so it is
    # admin-only. Blocks non-admin roles before any token is minted.
    from ..policy import require_raw_api
    require_raw_api()

    # Normalise path: ensure it starts with /open-apis/
    if not path.startswith("/open-apis/"):
        if path.startswith("/"):
            path = "/open-apis" + path
        else:
            path = "/open-apis/" + path

    # Parse body JSON
    json_body = None
    if body:
        try:
            json_body = json.loads(body)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid --body JSON: {e}")

    # Parse params JSON
    query_params = None
    if params:
        try:
            query_params = json.loads(params)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid --params JSON: {e}")

    api = _make_api(ctx)
    method_upper = method.upper()

    def _call(a: LarkAPI) -> dict:
        if method_upper == "GET":
            return a.get(path, params=query_params)
        elif method_upper in ("POST", "PUT", "PATCH", "DELETE"):
            return a._request(method_upper, path, json=json_body, params=query_params)
        else:
            raise click.ClickException(f"Unsupported method: {method}")

    try:
        data = _call(api)
    except LarkAPIError as e:
        if e.code == 99991668:
            # Token type mismatch — retry with the other identity
            current = ctx.obj.get("identity", "user")
            fallback = "bot" if current == "user" else "user"
            click.echo(f"Token type not supported, retrying as {fallback}...", err=True)
            api = _make_api(ctx, identity=fallback)
            try:
                data = _call(api)
            except LarkAPIError as e2:
                raise click.ClickException(str(e2))
        else:
            raise click.ClickException(str(e))

    click.echo(json.dumps(data, indent=2, ensure_ascii=False))
