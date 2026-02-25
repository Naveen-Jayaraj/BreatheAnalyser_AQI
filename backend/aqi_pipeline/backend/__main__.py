"""Run backend API with uvicorn."""

from __future__ import annotations

import argparse

from .config import get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AQI backend API server")
    parser.add_argument("--host", default=None, help="Bind host override")
    parser.add_argument("--port", type=int, default=None, help="Bind port override")
    parser.add_argument("--workers", type=int, default=None, help="Uvicorn worker count override")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    settings = get_settings()

    host = args.host or settings.host
    port = args.port or settings.port
    workers = max(1, int(args.workers or settings.workers))

    try:
        import uvicorn  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is required. Install with: pip install -e .[backend]") from exc

    uvicorn.run(
        "aqi_pipeline.backend.main:app",
        host=host,
        port=port,
        workers=workers,
        reload=bool(args.reload),
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
