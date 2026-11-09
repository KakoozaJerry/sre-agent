"""Command-line entry point. Grows with the roadmap phases."""

from __future__ import annotations

import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"--version", "version"}:
        print(f"sre-agent {__version__}")
        return 0
    print("sre-agent: incident commands arrive with Phase 1 (see PLAN.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
