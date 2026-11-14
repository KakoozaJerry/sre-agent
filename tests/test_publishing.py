"""The release publishing package (Phase 11, distribution).

The workflow file and this guide are the repo half of real
distribution, prepared the way the CI workflow was: every
step simulated and pinned here first, the file itself placed
on the website by the maintainer (the connection this
project is built with cannot write workflow files), and the
roadmap item checked off only when a package reads back on
PyPI and an image on GHCR.

These tests pin facts, not sentences: trusted publishing
with no stored PyPI token, the exact environment and image
names the maintainer steps depend on, and the guide's exact
pending-publisher values -- a workflow filename or
environment name that drifts from the guide would make the
first publish fail at PyPI, word for word.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_publish_workflow_uses_trusted_publishing_with_no_stored_token() -> None:
    text = _read(REPO_ROOT / ".github" / "workflows" / "publish.yml")
    for needle in (
        "release:",
        "types: [published]",
        "workflow_dispatch:",
        "name: pypi",
        "id-token: write",
        "pypa/gh-action-pypi-publish@release/v1",
        "python -m build",
        "twine check dist/*",
    ):
        assert needle in text
    # Trusted publishing means exactly this: no PyPI API token
    # or password is stored or referenced anywhere.
    assert "PYPI_API_TOKEN" not in text
    assert "password:" not in text.split("Publish the demo image")[0]


def test_publish_workflow_pushes_the_hosted_demo_image_to_ghcr() -> None:
    text = _read(REPO_ROOT / ".github" / "workflows" / "publish.yml")
    for needle in (
        "packages: write",
        "docker/login-action@v3",
        "registry: ghcr.io",
        "secrets.GITHUB_TOKEN",
        "docker/build-push-action@v6",
        "file: Dockerfile.hosted",
        # GHCR names are lowercase; the image is the
        # public-safe hosted demo, not the local server.
        "ghcr.io/kakoozajerry/sre-agent-demo",
    ):
        assert needle in text


def test_publishing_guide_names_the_exact_pending_publisher() -> None:
    flat = _flat(_read(REPO_ROOT / "docs" / "publishing.md"))
    for needle in (
        "PyPI Project Name: `sre-agent-cli`",
        "Owner: `KakoozaJerry`",
        "Repository name: `sre-agent`",
        "Workflow filename: `publish.yml`",
        "Environment name: `pypi`",
        "pending publisher",
        "trusted publishing",
        "no PyPI API token",
    ):
        assert needle in flat


def test_publishing_guide_ends_in_read_backs_not_a_green_tick() -> None:
    flat = _flat(_read(REPO_ROOT / "docs" / "publishing.md"))
    for needle in (
        "https://pypi.org/pypi/sre-agent-cli/json",
        "`pipx install sre-agent-cli`",
        "`sre-agent --version`",
        "docker pull ghcr.io/kakoozajerry/sre-agent-demo:main",
        "visibility to public",
    ):
        assert needle in flat
