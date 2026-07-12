"""Single entry point for MacroEdge command-line tools.

Examples:

    python -m macroedge demo
    python -m macroedge contracts validate --input macroedge/examples/contract-draft.example.json
    python -m macroedge journal summary --ledger macroedge/ledger.jsonl
    python -m macroedge kalshi validate --input macroedge/examples/kalshi-market-cpi.example.json --event-type cpi

This dispatcher is only a convenience wrapper. The underlying tools remain
offline-only and never place or execute trades.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence


COMMANDS = {
    "contracts": ("macroedge.app.contracts", "Contract observation tools"),
    "journal": ("macroedge.app.journal", "Trade-candidate journal, settlements, performance, and dashboard tools"),
    "kalshi": ("macroedge.app.kalshi", "Offline Kalshi fixture adapter tools"),
    "demo": ("demo", "Run the end-to-end offline MacroEdge demo"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m macroedge",
        description="MacroEdge offline macro event-contract research tools.",
    )
    parser.add_argument(
        "command",
        choices=sorted(COMMANDS),
        help="Tool to run: " + ", ".join(f"{name} ({description})" for name, (_, description) in sorted(COMMANDS.items())),
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to the selected tool",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv:
        build_parser().print_help()
        return 0

    parser = build_parser()
    namespace = parser.parse_args(argv)
    module_name = COMMANDS[namespace.command][0]
    module = importlib.import_module(module_name)
    tool_main = getattr(module, "main", None)
    if tool_main is None:
        parser.error(f"{namespace.command} does not expose a main() function")
    if namespace.command == "demo":
        if namespace.args:
            parser.error("demo does not accept additional arguments")
        return int(tool_main())
    return int(tool_main(namespace.args))


if __name__ == "__main__":
    raise SystemExit(main())
