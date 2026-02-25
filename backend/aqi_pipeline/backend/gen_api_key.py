"""Generate secure API keys for backend authentication."""

from __future__ import annotations

import argparse
import secrets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate AQI backend API key")
    parser.add_argument("--bytes", type=int, default=32, help="Entropy bytes (default: 32)")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    entropy = max(16, int(args.bytes))
    print(secrets.token_urlsafe(entropy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

