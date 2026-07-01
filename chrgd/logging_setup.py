"""Structured logging setup.

One place to configure the `chrgd` logger tree. Messages use a compact
key=value style (e.g. ``run.build built=2 spend=0.1234``) so logs are
greppable and machine-parseable without a JSON pipeline. Call
`configure_logging()` once at process start (CLI + web do this).
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("chrgd")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"chrgd.{name}")
