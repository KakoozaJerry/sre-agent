"""Tests for the README terminal recording (Phase 11).

The recording is a claim like any other: it must come from a
real run, last the 60-90 seconds the roadmap asks for, show the
four stages (alert, diagnosis, approve, resolved), and be
embedded in the README under the pitch. These tests pin the
artefacts; ``demo/make_walkthrough_recording.py`` is what
verifies the values against a live server when it is made.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CAST = REPO_ROOT / "demo" / "walkthrough.cast"
SVG = REPO_ROOT / "docs" / "walkthrough.svg"
GENERATOR = REPO_ROOT / "demo" / "make_walkthrough_recording.py"


def _events() -> tuple[dict, list]:
    lines = CAST.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    return header, [json.loads(line) for line in lines[1:]]


def test_cast_is_a_valid_asciinema_recording_of_the_right_length() -> None:
    header, events = _events()
    assert header["version"] == 2
    assert events and all(event[1] == "o" for event in events)
    duration = events[-1][0]
    assert 60.0 <= duration <= 90.0, f"recording lasts {duration}s, want 60-90"


def test_cast_shows_the_four_walkthrough_stages() -> None:
    _, events = _events()
    text = "".join(event[2] for event in events)
    for needle in (
        "HighErrorRate",
        "status: diagnosed",
        "Most likely: A call that checkout depends on is timing out",
        "Evidence (8 record(s)):",
        "require_approval",
        "Restarted checkout: restarts now 1, failing state cleared.",
        "mitigating",
        "resolved: true",
        "status: resolved",
    ):
        assert needle in text, f"recording is missing: {needle}"


def test_svg_is_the_same_recording_animated_and_looping() -> None:
    text = SVG.read_text(encoding="utf-8")
    root = ET.fromstring(text)  # must be valid XML or GitHub shows nothing
    assert root.tag.endswith("svg")
    assert 'repeatCount="indefinite"' in text
    match = re.search(r'dur="([0-9.]+)s"', text)
    assert match is not None
    assert 66.0 <= float(match.group(1)) <= 96.0
    for needle in ("HighErrorRate", "require_approval", "status: resolved"):
        assert needle in text, f"SVG is missing: {needle}"


def test_readme_embeds_the_recording_under_the_pitch() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    above = readme.split("## In plain English", 1)[0]
    assert "![Terminal recording: one alert, end to end](docs/walkthrough.svg)" in above
    assert "demo/walkthrough.cast" in above


def test_generator_runs_the_real_walkthrough_and_checks_its_values() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    # The generator must start the shipped server and check the
    # real replies, not type a script from memory.
    for needle in (
        "sre_agent.server:app",
        '"/webhooks/generic"',
        '"restart-workload"',
        'alert_out["status"] == "diagnosed"',
        'approval["incident_status"] == "mitigating"',
        'closed["resolved"] is True',
        "60.0 <= rec.duration <= 90.0",
    ):
        assert needle in source, f"generator is missing its check: {needle}"
