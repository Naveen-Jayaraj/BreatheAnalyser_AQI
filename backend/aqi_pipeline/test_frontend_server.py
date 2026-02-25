"""Tiny local server to run the test frontend page without Django project scaffolding."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .naqi import naqi_category
from .physics_pipeline import predict_aqi_with_trace

HTML_PATH = Path(__file__).resolve().parent / "django_app" / "static" / "aqi_pipeline" / "test_lookup.html"


class TestFrontendHandler(BaseHTTPRequestHandler):
    server_version = "AQITestFrontend/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/test_lookup.html", "/static/aqi_pipeline/test_lookup.html"}:
            if not HTML_PATH.exists():
                self._send_json(
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    payload={"error": {"code": "missing_page", "message": f"Missing file: {HTML_PATH}"}},
                )
                return
            content = HTML_PATH.read_text(encoding="utf-8")
            self._send_bytes(
                status=HTTPStatus.OK,
                body=content.encode("utf-8"),
                content_type="text/html; charset=utf-8",
            )
            return

        self._send_json(
            status=HTTPStatus.NOT_FOUND,
            payload={"error": {"code": "not_found", "message": "Path not found"}},
        )

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/v1/aqi/lookup":
            self._send_json(
                status=HTTPStatus.NOT_FOUND,
                payload={"error": {"code": "not_found", "message": "Path not found"}},
            )
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(max(0, content_length)) if content_length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                status=HTTPStatus.BAD_REQUEST,
                payload={"error": {"code": "invalid_json", "message": "Body must be valid JSON"}},
            )
            return

        try:
            latitude = float(payload["latitude"])
            longitude = float(payload["longitude"])
        except (KeyError, TypeError, ValueError):
            self._send_json(
                status=HTTPStatus.BAD_REQUEST,
                payload={
                    "error": {
                        "code": "invalid_coordinate",
                        "message": "Body must contain numeric latitude and longitude",
                    }
                },
            )
            return

        try:
            value, trace = predict_aqi_with_trace(latitude, longitude, strict=False)
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                payload={
                    "error": {
                        "code": "prediction_failed",
                        "message": str(exc),
                    },
                },
            )
            return

        if value is None:
            self._send_json(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                payload={
                    "error": {"code": "no_sensor_data", "message": "No nearby sensor data available"},
                    "trace": trace,
                    "source": "local_test_frontend_server",
                },
            )
            return

        rounded = int(round(float(value)))
        self._send_json(
            status=HTTPStatus.OK,
            payload={
                "aqi": rounded,
                "aqi_raw": float(value),
                "category": naqi_category(rounded),
                "source": "local_test_frontend_server",
                "trace": trace,
            },
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # Keep console output concise for local manual testing.
        return

    def _send_json(self, *, status: HTTPStatus, payload: dict) -> None:
        self._send_bytes(
            status=status,
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            content_type="application/json; charset=utf-8",
        )

    def _send_bytes(self, *, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local AQI test frontend server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), TestFrontendHandler)
    print(f"aqi_test_frontend_server running at http://{args.host}:{args.port}")
    print(f"open http://{args.host}:{args.port}/test_lookup.html")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
