"""Packaging metadata tests (Phase 11, distribution).

The metadata in ``pyproject.toml`` is what PyPI will show
when the package is published. These tests pin the facts —
URLs, keywords, classifiers — so a later edit cannot drop
them quietly, and pin the README's honest statement that
the package is not on PyPI yet.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _project() -> dict:
    with (REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]


def test_project_urls_point_at_the_real_repository() -> None:
    urls = _project()["urls"]
    base = "https://github.com/KakoozaJerry/sre-agent"
    assert urls["Homepage"] == base
    assert urls["Repository"] == base
    assert urls["Issues"] == f"{base}/issues"
    assert urls["Changelog"] == f"{base}/blob/main/CHANGELOG.md"
    assert urls["Documentation"].startswith(base)


def test_keywords_match_the_positioning_topics() -> None:
    keywords = _project()["keywords"]
    for topic in ("sre", "ai-agent", "incident-response", "observability", "devops"):
        assert topic in keywords


def test_classifiers_cover_licence_python_and_audience() -> None:
    classifiers = _project()["classifiers"]
    assert "License :: OSI Approved :: MIT License" in classifiers
    assert "Development Status :: 4 - Beta" in classifiers
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in classifiers
    assert "Intended Audience :: System Administrators" in classifiers


def test_requires_python_matches_the_classifier_floor() -> None:
    project = _project()
    assert project["requires-python"] == ">=3.11"


def test_readme_documents_pipx_as_the_future_user_path_honestly() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    flat = " ".join(readme.split())
    assert "pipx install sre-agent" in flat
    assert "not on PyPI yet" in flat
    # The contributor quickstart stays the install path today.
    assert "./quickstart.sh" in readme
