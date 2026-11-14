"""The demo sample service: a tiny service that can break on purpose.

This is the service the demo environment watches. It is not part of
the agent. It is the patient, not the doctor.

In plain words:

- It serves a pretend checkout service for a shop.
- Every request to ``/work`` is counted. When the service is told
  to fail (the "chaos" switch), those requests start returning
  errors, and the error count goes up.
- It publishes its counts at ``/metrics`` in the text format
  Prometheus understands. The two metric names are exactly the
  ones the agent's reasoning loop asks about
  (``http_requests_total`` and ``http_request_errors_total``), so
  the demo, the alert rule, and the diagnosis all talk about the
  same numbers.
- It also records one log line per request it serves and serves
  them at ``/logs``, so the agent's live demo diagnosis (Phase
  12) reads log lines this service really produced — a failing
  request's line carries the same "downstream call timed out"
  detail its ``/work`` reply carries — instead of seeded lines.
- It uses only the Python standard library, so its container
  image is small and there is nothing extra to install.

Routes:

- ``GET /`` — a short description, as JSON.
- ``GET /health`` — 200 when well, 503 when the chaos switch is on.
- ``GET /work`` — one pretend request: 200 when well, 500 when failing.
- ``GET /metrics`` — the counters, in Prometheus text format.
- ``GET /logs`` — the request log lines recorded so far, as JSON.
- ``GET /chaos`` — is the chaos switch on or off?
- ``POST /chaos/on`` — start failing.
- ``POST /chaos/off`` — stop failing (recover).

Run it directly with ``python app.py``; the port comes from the
``PORT`` environment variable (default 8000). Setting
``CHAOS_ERRORS=1`` starts the service already failing.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_LOG_ENTRIES = 100


class SampleService:
    """The countable, breakable core. Kept apart from the HTTP
    layer so tests can check the behaviour without any network."""

    def __init__(
        self,
        service: str = "checkout",
        namespace: str = "shop",
        environment: str = "demo",
        failing: bool = False,
    ) -> None:
        self.service = service
        self.namespace = namespace
        self.environment = environment
        self.failing = failing
        self.requests_total = 0
        self.errors_total = 0
        self.log_entries: list[dict[str, str]] = []

    def set_failing(self, failing: bool) -> None:
        self.failing = failing

    def _record_log(self, level: str, message: str) -> None:
        self.log_entries.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "service": self.service,
                "namespace": self.namespace,
                "level": level,
                "message": message,
            }
        )
        # Bounded, like every other record in this project: only
        # the newest lines are kept.
        del self.log_entries[:-MAX_LOG_ENTRIES]

    def health(self) -> tuple[int, dict[str, str]]:
        if self.failing:
            return 503, {"status": "failing", "service": self.service}
        return 200, {"status": "ok", "service": self.service}

    def work(self) -> tuple[int, dict[str, str]]:
        """Do one pretend request. Failing requests are errors."""
        self.requests_total += 1
        if self.failing:
            self.errors_total += 1
            self._record_log("ERROR", "downstream call timed out")
            return 500, {"result": "error", "detail": "downstream call timed out"}
        self._record_log("INFO", "checkout request completed")
        return 200, {"result": "ok"}

    def render_metrics(self) -> str:
        """The counters in Prometheus text format.

        The labels name the same target the alert rule uses, so an
        alert from this demo groups onto the right incident.
        """
        labels = (
            f'service="{self.service}",namespace="{self.namespace}",'
            f'environment="{self.environment}"'
        )
        return (
            "# HELP http_requests_total Total requests served by the demo service.\n"
            "# TYPE http_requests_total counter\n"
            f"http_requests_total{{{labels}}} {self.requests_total}\n"
            "# HELP http_request_errors_total Total failed requests in the demo.\n"
            "# TYPE http_request_errors_total counter\n"
            f"http_request_errors_total{{{labels}}} {self.errors_total}\n"
        )


def make_handler(service: SampleService) -> type[BaseHTTPRequestHandler]:
    """Build an HTTP handler that serves one ``SampleService``."""

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, body: dict[str, object]) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/":
                self._send_json(
                    200,
                    {
                        "service": service.service,
                        "what": "demo sample service; see /work, /metrics, /chaos",
                    },
                )
            elif self.path == "/health":
                code, body = service.health()
                self._send_json(code, body)
            elif self.path == "/work":
                code, body = service.work()
                self._send_json(code, body)
            elif self.path == "/metrics":
                data = service.render_metrics().encode("utf-8")
                self.send_response(200)
                self.send_header(
                    "Content-Type", "text/plain; version=0.0.4; charset=utf-8"
                )
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/logs":
                self._send_json(200, {"entries": list(service.log_entries)})
            elif self.path == "/chaos":
                self._send_json(200, {"failing": service.failing})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path == "/chaos/on":
                service.set_failing(True)
                self._send_json(200, {"failing": True})
            elif self.path == "/chaos/off":
                service.set_failing(False)
                self._send_json(200, {"failing": False})
            else:
                self._send_json(404, {"error": "not found"})

        def log_message(self, format: str, *args: object) -> None:
            # Keep the demo quiet; Prometheus and the agent keep the records.
            return

    return Handler


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    service = SampleService(failing=os.environ.get("CHAOS_ERRORS") == "1")
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(service))
    print(f"sample service listening on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
