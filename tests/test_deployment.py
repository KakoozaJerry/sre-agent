"""The hosted demo deployment package (Phase 10).

The deployment item is not done — there is no public URL yet —
but everything that does not need Abraham's own Render account
is built and pinned here:

- ``render.yaml`` names the service, the free plan ($0), the
  hosted image and the health check, and carries no secret: the
  demo token is ``sync: false``, typed by Abraham in Render's
  dashboard when he applies the Blueprint, never stored in this
  repo;
- ``Dockerfile.hosted`` runs the hosted demo factory — the
  build that refuses to start without a token — as a non-root
  user, installs from the hash-checked lock file, and listens
  on the port the host assigns (``$PORT``);
- the guide names the host and the cost, states the free plan's
  limits plainly, and says the remaining step is Abraham's
  sign-in, so no reader mistakes the package for a deployment.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def test_render_blueprint_names_the_free_service() -> None:
    text = _read(REPO_ROOT / "render.yaml")
    for needle in (
        "type: web",
        "name: sre-agent-demo",
        "runtime: docker",
        "dockerfilePath: ./Dockerfile.hosted",
        "plan: free",
        "region: oregon",
        "healthCheckPath: /health",
    ):
        assert needle in text


def test_render_blueprint_carries_no_secret() -> None:
    text = _read(REPO_ROOT / "render.yaml")
    # The token is asked for at apply time, never written here:
    # its value must not appear, under any spelling Render uses.
    assert "SRE_AGENT_API_TOKEN" in text
    assert "sync: false" in text
    assert "value:" not in text


def test_hosted_image_runs_the_hosted_factory_on_the_hosts_port() -> None:
    text = _read(REPO_ROOT / "Dockerfile.hosted")
    for needle in (
        "sre_agent.hosted_demo:create_hosted_demo_app",
        "--factory",
        "${PORT:-8080}",
        "--host 0.0.0.0",
        # The F-06 rules carry over: hash-checked lock install,
        # and the server does not run as root.
        "pip install --no-cache-dir --require-hashes -r requirements.lock",
        "USER sreagent",
    ):
        assert needle in text
    # The hosted image must not quietly run the plain local
    # server, which is allowed to start with no token.
    assert "sre_agent.server:app" not in text


def test_hosted_demo_guide_names_host_cost_and_the_live_url() -> None:
    text = _read(REPO_ROOT / "docs" / "hosted-demo.md")
    for needle in (
        "Render",
        "$0",
        "free web service",
        "render.yaml",
        "Dockerfile.hosted",
        # The free plan's real limits, stated in the guide:
        # it sleeps when idle and hours are capped.
        "15 minutes",
        "750",
        # The health check that watches the URL.
        "/health",
        # The deployment happened on 2026-11-12 (the owner's own
        # sign-in step, as the guide records); the guide must
        # name the real public address, and must no longer claim
        # there is none.
        "Abraham",
        "https://sre-agent-demo.onrender.com",
    ):
        assert needle in text
    assert "no public URL yet" not in text
