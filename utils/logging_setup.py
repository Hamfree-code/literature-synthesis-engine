"""Structured logging with rich console output."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

from config.settings import settings

console = Console()


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )
