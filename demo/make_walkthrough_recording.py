"""Make the README terminal recording from a real walkthrough run.

In plain English: this script starts the shipped server on port
8080, runs the README walkthrough against it for real -- send the
demo checkout alert, read the diagnosis with the CLI, propose the
restart, approve it by name, close the incident with a resolved
alert -- and records what a visitor would see, paced for reading.

Two files come out, both plain text so they can travel through
the project's file-upload route (a binary GIF cannot):

- ``demo/walkthrough.cast`` -- an asciinema recording (version 2
  format: one JSON header line, then one timed event per line).
  Play it with ``asciinema play demo/walkthrough.cast``.
- ``docs/walkthrough.svg`` -- the same lines as an animated SVG,
  which is the copy the README embeds.

Honesty rules, enforced here before anything is written: the
diagnosis, the evidence count, the approval decision, the
approval summary and the final resolved status are all read from
the real replies and checked against the values the recording
shows; the ``sre-agent show`` lines are copied from the CLI's
real output and checked word for word. Output lines are
shortened for the screen (a full ``show`` is long) -- the values
shown are the real ones, and the full walkthrough is the text
one in the README. The pauses are reading pauses, added on
purpose so the whole recording lasts 60-90 seconds; nothing
about the agent's own speed is claimed by them.

Run it from the repo root, after the quickstart install:

    .venv/bin/python demo/make_walkthrough_recording.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parent.parent
PORT = 8080
BASE = f"http://127.0.0.1:{PORT}"
CAST_PATH = REPO_ROOT / "demo" / "walkthrough.cast"
SVG_PATH = REPO_ROOT / "docs" / "walkthrough.svg"
HOLD_SECONDS = 6.0  # the last screen stays up this long, then loops

ALERT = {
    "alert_name": "HighErrorRate",
    "service": "checkout",
    "namespace": "shop",
    "environment": "demo",
    "summary": "Checkout errors are above 10%",
}


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode()


class Recording:
    """Timed screen blocks: (seconds from start, [lines])."""

    def __init__(self) -> None:
        self.blocks: list[tuple[float, list[str]]] = []
        self.started = time.monotonic()

    def wait_until(self, offset: float) -> None:
        delay = offset - (time.monotonic() - self.started)
        if delay > 0:
            time.sleep(delay)

    def emit(self, at: float, lines: list[str]) -> None:
        self.wait_until(at)
        stamp = round(time.monotonic() - self.started, 2)
        self.blocks.append((stamp, lines))

    @property
    def duration(self) -> float:
        return self.blocks[-1][0] if self.blocks else 0.0


def _run_walkthrough(rec: Recording) -> None:
    # 1. Health (the server was started by main, before recording).
    _, health = _request("GET", "/health")
    assert json.loads(health) == {"status": "ok"}, health
    rec.emit(
        0.0,
        [
            "sre-agent -- one alert, end to end (seeded demo data only)",
            "",
            "$ uvicorn sre_agent.server:app --port 8080   # terminal 1: the server",
            "$ curl http://localhost:8080/health          # terminal 2",
            health.strip(),
        ],
    )

    # 2. Send the demo alert; the server diagnoses it at ingestion.
    status, reply = _request("POST", "/webhooks/generic", ALERT)
    alert_out = json.loads(reply)
    assert status == 200 and alert_out["created"] is True, reply
    assert alert_out["status"] == "diagnosed", reply
    diagnosis = alert_out["diagnosis"]
    assert diagnosis.startswith("Most likely:"), reply
    incident_id = alert_out["incident_id"]
    rec.emit(
        10.0,
        [
            "",
            "$ curl -X POST http://localhost:8080/webhooks/generic \\",
            "    -d '{\"alert_name\":\"HighErrorRate\",\"service\":\"checkout\",...}'",
            f"created: true   status: {alert_out['status']}",
            f"diagnosis: {diagnosis}",
        ],
    )

    # 3. Read the diagnosis with the real CLI; lines copied verbatim.
    env = {**os.environ, "SRE_AGENT_BASE_URL": BASE}
    shown = subprocess.run(
        [sys.executable, "-m", "sre_agent.cli", "show", incident_id],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=True,
    ).stdout
    wanted = [
        "Status: diagnosed",
        f"Diagnosis: {diagnosis}",
        "- A call that checkout depends on is timing out (confidence 0.85)",
        "Evidence (8 record(s)):",
    ]
    for line in wanted:
        assert line in shown.splitlines(), f"CLI did not print: {line}"
    rec.emit(24.0, ["", "$ sre-agent show <incident-id>", *wanted])

    # 4. Propose the restart; Tier 2 means it waits for a human.
    status, reply = _request(
        "POST", f"/incidents/{incident_id}/actions", {"runbook_id": "restart-workload"}
    )
    proposal = json.loads(reply)
    assert status == 201 and proposal["decision"] == "require_approval", reply
    pending = proposal["pending"]
    assert pending["replicas_affected"] == 2, reply
    rec.emit(
        40.0,
        [
            "",
            "$ curl -X POST .../incidents/<incident-id>/actions \\",
            "    -d '{\"runbook_id\":\"restart-workload\"}'",
            "201  decision: require_approval (Tier 2: a human must approve)",
            "pending: restart-workload, touches 2 replicas, expires in 15 minutes",
        ],
    )

    # 5. Approve it by name; the fix runs and the incident mitigates.
    status, reply = _request(
        "POST",
        f"/incidents/{incident_id}/approvals/{pending['id']}/approve",
        {"by": "operator"},
    )
    approval = json.loads(reply)
    assert status == 200 and approval["decision"] == "approved", reply
    assert approval["incident_status"] == "mitigating", reply
    rec.emit(
        50.0,
        [
            "",
            "$ curl -X POST .../approvals/<pending-id>/approve -d '{\"by\":\"operator\"}'",
            approval["summary"],
            "incident_status: mitigating  (a run fix is not yet proof it worked)",
        ],
    )

    # 6. The resolved alert closes the incident.
    status, reply = _request("POST", "/webhooks/generic", {**ALERT, "status": "resolved"})
    closed = json.loads(reply)
    assert status == 200 and closed["resolved"] is True, reply
    assert closed["status"] == "resolved", reply
    rec.emit(
        64.0,
        [
            "",
            "$ curl -X POST http://localhost:8080/webhooks/generic \\",
            "    -d '{...,\"status\":\"resolved\"}'",
            "resolved: true   status: resolved",
            "",
            "Done: alert -> diagnosis -> approve -> resolved.",
        ],
    )
    rec.emit(
        74.0,
        ["", "(End of recording. The same walkthrough in text is in the README.)"],
    )


def _write_cast(rec: Recording) -> None:
    header = {
        "version": 2,
        "width": 100,
        "height": 36,
        "timestamp": int(time.time()),
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        "title": "sre-agent: one alert, end to end",
    }
    lines = [json.dumps(header)]
    for stamp, block in rec.blocks:
        text = "".join(line + "\r\n" for line in block)
        lines.append(json.dumps([stamp, "o", text]))
    CAST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_svg(rec: Recording) -> None:
    total = rec.duration + HOLD_SECONDS
    flat: list[tuple[float, str]] = [
        (stamp, line) for stamp, block in rec.blocks for line in block
    ]
    width, line_h, top = 980, 21, 58
    height = top + line_h * len(flat) + 16
    out = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
            'aria-label="Terminal recording: one alert, end to end">'
        ),
        "<title>sre-agent: one alert, end to end</title>",
        f'<rect width="{width}" height="{height}" rx="8" fill="#0d1117"/>',
        (
            '<circle cx="20" cy="22" r="6" fill="#ff5f56"/>'
            '<circle cx="42" cy="22" r="6" fill="#ffbd2e"/>'
            '<circle cx="64" cy="22" r="6" fill="#27c93f"/>'
        ),
        (
            '<text x="84" y="27" font-family="monospace" font-size="13" '
            'fill="#8b949e">sre-agent -- one alert, end to end</text>'
        ),
    ]
    for i, (stamp, line) in enumerate(flat):
        y = top + i * line_h
        colour = "#7ee787" if line.startswith("$") else "#c9d1d9"
        appear = round(stamp / total, 4)
        fade = round((total - 3.0) / total, 4)
        shown_at = round(min((stamp + 0.3) / total, fade), 4)
        out.append(
            f'<text x="16" y="{y}" font-family="monospace" font-size="14" '
            f'fill="{colour}" opacity="0">{escape(line) or " "}'
            f'<animate attributeName="opacity" values="0;0;1;1;0" '
            f'keyTimes="0;{appear};{shown_at};{fade};1" '
            f'dur="{total:.1f}s" repeatCount="indefinite"/></text>'
        )
    out.append("</svg>")
    SVG_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sre_agent.server:app", "--port", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            try:
                _request("GET", "/health")
                break
            except OSError:  # not up yet: connection refused or timed out
                time.sleep(0.2)
        else:
            print("recording: the server did not come up", file=sys.stderr)
            return 1
        rec = Recording()
        _run_walkthrough(rec)
    finally:
        server.terminate()
        server.wait(timeout=10)
    if not 60.0 <= rec.duration <= 90.0:
        print(f"recording: lasts {rec.duration:.1f}s, want 60-90s", file=sys.stderr)
        return 1
    _write_cast(rec)
    _write_svg(rec)
    print(f"wrote {CAST_PATH.relative_to(REPO_ROOT)} and {SVG_PATH.relative_to(REPO_ROOT)}")
    print(f"duration: {rec.duration:.1f}s of walkthrough, {len(rec.blocks)} screens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
