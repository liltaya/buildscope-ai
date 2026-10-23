"""Command-line entry point for BuildScope AI."""

from __future__ import annotations

from buildscope import version
from buildscope.logging import configure_logging


def main() -> int:
    """Run the BuildScope command-line entry point."""
    logger = configure_logging()

    logger.info(
        "BuildScope AI starting",
    )

    print(f"BuildScope AI {version()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
