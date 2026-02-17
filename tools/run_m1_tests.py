"""Run M1 tests by layer: UT, CT, E2E."""

from __future__ import annotations

import argparse
import subprocess
import sys


TEST_GROUPS = {
    "ut": ["tests/unit", "-m", "unit"],
    "ct": ["tests/component", "-m", "component"],
    "e2e": ["tests/e2e", "-m", "e2e"],
    "all": ["tests/unit", "tests/component", "-m", "unit or component"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 test runner")
    parser.add_argument("target", nargs="?", default="all", choices=tuple(TEST_GROUPS))
    parser.add_argument("--list", action="store_true", help="list supported targets")
    args = parser.parse_args()

    if args.list:
        print("ut")
        print("ct")
        print("e2e")
        print("all")
        return 0

    cmd = [sys.executable, "-m", "pytest", *TEST_GROUPS[args.target]]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
