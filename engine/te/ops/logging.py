"""Logging configuration — structlog wired through stdlib `logging`, so every
module's existing `logging.getLogger(__name__)` call (the pattern already
used across `te/`, e.g. `te/engine/scheduler.py`) is captured without having
to migrate call sites to structlog's own API.

Dev vs prod is the only axis that matters here:

- **dev** renders colored, human-readable single lines at `DEBUG` and above.
  Every module's `logger.debug(...)` call is meant to be read by a person
  understanding what the engine just did — e.g. "a bar was appended" or "the
  paper cycle skipped this run and why" — so dev shows all of it.
- **prod** renders one-line JSON at `INFO` and above. `DEBUG`-level detail
  that exists purely to help a developer understand dev-time behaviour never
  reaches a shipped prod log stream, and what does ship is machine-parseable
  (one JSON object per line) rather than colored terminal text no log
  aggregator can parse.

Call `configure_logging(settings.env)` once, as early as possible in process
startup (`te.api.main.create_app`) — before any other module's logger is
used, since `structlog.configure`/`logging.basicConfig`-equivalent setup
only takes effect for loggers created or used after it runs.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog


def configure_logging(env: Literal["dev", "prod"]) -> None:
    is_prod = env == "prod"
    min_level = logging.INFO if is_prod else logging.DEBUG

    # Shared by both the structlog-native path and the stdlib-logging path
    # below (`foreign_pre_chain`) so a `logging.getLogger(__name__).info(...)`
    # call from e.g. scheduler.py gets the same timestamp/level/cycle_id
    # treatment as a native structlog call.
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if is_prod
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(min_level)
