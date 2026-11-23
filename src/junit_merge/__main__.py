"""Command line entry point: ``python -m junit_merge`` or ``junit-merge``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import POLICIES, merge, parse_file, to_xml


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="junit-merge",
        description="Merge JUnit XML reports into one, recomputing the counts.",
    )
    parser.add_argument("reports", nargs="+", type=Path, metavar="REPORT", help="JUnit XML files")
    parser.add_argument("-o", "--out", type=Path, help="write the merged report here")
    parser.add_argument(
        "--on-duplicate",
        choices=POLICIES,
        default="keep-worst",
        help="what to do with a test that appears in several reports (default: keep-worst)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit with status 1 when the merged report contains failures or errors",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    missing = [str(path) for path in args.reports if not path.is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        report = merge(
            [parse_file(path) for path in args.reports],
            on_duplicate=args.on_duplicate,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.out:
        args.out.write_bytes(to_xml(report))
    print(
        f"{report.tests} tests, {report.failures} failures, "
        f"{report.errors} errors, {report.skipped} skipped, {report.time:.2f}s"
    )
    if args.check and (report.failures or report.errors):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
