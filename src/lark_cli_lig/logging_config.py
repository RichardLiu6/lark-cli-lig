"""3-level logging: file always DEBUG, console varies by -v / -vv."""

import logging
import re
from datetime import datetime
from pathlib import Path


class SensitiveFilter(logging.Filter):
    """Redact tokens and secrets from log output."""

    PATTERNS = ["Bearer ", "app_secret", "access_token"]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern in self.PATTERNS:
            if pattern in msg:
                msg = re.sub(
                    r'(Bearer |access_token["\s:=]+)([a-zA-Z0-9_-]{8})[a-zA-Z0-9_-]+',
                    r"\1\2***REDACTED***",
                    msg,
                )
                record.msg = msg
                record.args = ()
        return True


def setup_logging(verbosity: int = 0) -> logging.Logger:
    """Configure logging.

    verbosity=0: only results (WARNING+ to console, DEBUG to file)
    verbosity=1 (-v): API request/response summary to console
    verbosity=2 (-vv): full request/response + headers to console
    """
    log_dir = Path.home() / ".lark-cli-lig" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.log"

    logger = logging.getLogger("lark_cli_lig")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    sensitive_filter = SensitiveFilter()

    # File handler - always DEBUG level, persists everything
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    file_handler.addFilter(sensitive_filter)

    # Console handler - level based on verbosity
    console_handler = logging.StreamHandler()
    if verbosity >= 2:
        console_handler.setLevel(logging.DEBUG)
    elif verbosity >= 1:
        console_handler.setLevel(logging.INFO)
    else:
        console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    console_handler.addFilter(sensitive_filter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
