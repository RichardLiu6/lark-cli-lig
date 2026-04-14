"""LIG Lark CLI - entry point with Click command groups."""

import click

from .logging_config import setup_logging


@click.group()
@click.option(
    "--as",
    "identity",
    type=click.Choice(["user", "bot"]),
    default="user",
    help="Execute as user (OAuth, default) or bot (tenant token, admin only)",
)
@click.option("-v", "--verbose", count=True, help="Verbosity: -v summary, -vv debug")
@click.pass_context
def cli(ctx: click.Context, identity: str, verbose: int) -> None:
    """LIG Lark CLI - Messaging, Approvals, Contacts, Bitable"""
    ctx.ensure_object(dict)
    ctx.obj["identity"] = identity
    ctx.obj["verbose"] = verbose
    setup_logging(verbose)

    # Check config on all commands except 'auth' (which is used for setup)
    from .config import require_app_config
    if ctx.invoked_subcommand != "auth":
        require_app_config()


# ── IM commands (from commands/im.py) ─────────────────────────

from .commands.im import send, send_image, send_file, send_group, read

cli.add_command(send)
cli.add_command(send_image, "send-image")
cli.add_command(send_file, "send-file")
cli.add_command(send_group, "send-group")
cli.add_command(read)

# ── Contacts commands (from commands/contacts.py) ─────────────

from .commands.contacts import users, users_all

cli.add_command(users)
cli.add_command(users_all, "users-all")

# ── Chats command (from commands/chats.py) ────────────────────

from .commands.chats import chats

cli.add_command(chats)


# ── Auth group (from commands/auth_cmd.py) ────────────────────

from .commands.auth_cmd import auth

cli.add_command(auth)

# ── Approval group (from commands/approval.py) ────────────────

from .commands.approval import approval

cli.add_command(approval)

# ── Download command (from commands/download.py) ───────────────

from .commands.download import download

cli.add_command(download)

# ── Generic API command (from commands/raw.py) ────────────────

from .commands.raw import api_cmd

cli.add_command(api_cmd, "api")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
