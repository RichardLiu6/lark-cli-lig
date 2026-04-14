"""Configuration: loads .env from search chain, exports constants."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

CONFIG_DIR: Path = Path.home() / ".lark-cli-lig"
LOG_DIR: Path = CONFIG_DIR / "logs"

# .env search chain: CWD → ~/.lark-cli-lig/ → legacy path → env vars only
_ENV_SEARCH = [
    Path.cwd() / ".env",
    CONFIG_DIR / ".env",
    Path.home() / "Documents" / "LIG_ALL" / "lark" / ".env",  # legacy
]

_env_loaded = False
for _p in _ENV_SEARCH:
    if _p.exists():
        load_dotenv(_p)
        _env_loaded = True
        break

if not _env_loaded:
    load_dotenv()  # try env vars only

APP_ID: str = os.getenv("LARK_APP_ID", "")
APP_SECRET: str = os.getenv("LARK_APP_SECRET", "")
DOMAIN: str = os.getenv("LARK_DOMAIN", "https://open.larksuite.com")


def require_app_config() -> None:
    """Check APP_ID and APP_SECRET are set. Print helpful message if not."""
    if not APP_ID or not APP_SECRET:
        print(
            "Error: LARK_APP_ID and LARK_APP_SECRET not configured.\n"
            "\n"
            "Setup options (pick one):\n"
            f"  1. Create {CONFIG_DIR / '.env'} with:\n"
            "       LARK_APP_ID=cli_xxxxx\n"
            "       LARK_APP_SECRET=xxxxx\n"
            "  2. Create .env in current directory (same format)\n"
            "  3. Set environment variables directly\n"
            "\n"
            "Get these from: Lark Developer Console → Your App → Credentials",
            file=sys.stderr,
        )
        sys.exit(1)
