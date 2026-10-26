#!/usr/bin/env python3
"""Plan a deploy to the only allowed target. This does not contact a server."""

import argparse
import sys


def build_parser():
    parser = argparse.ArgumentParser(description="Plan a deploy to an allowed target.")
    parser.add_argument("--target", required=True, choices=("staging",))
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    print(f"plan only: {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
