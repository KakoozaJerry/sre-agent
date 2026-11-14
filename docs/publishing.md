# Publishing a release — in plain English

Phase 11's last item is real distribution: this project on
PyPI, so a user can run `pipx install sreagent`, and the demo
image on GHCR (GitHub's container registry). The package
itself is ready — the name Abraham chose is `sreagent`, the
metadata is complete and proven in a built wheel, and the
release workflow is prepared. What is left is a short set of
maintainer steps, because publishing needs Abraham's own
PyPI account and one workflow file placed on the GitHub
website. This page is those steps, in order, with the checks
that prove each one worked.

Nothing here has been published yet. As of 13 November 2026,
PyPI returns 404 for `sreagent` (the name is free, not taken),
and GHCR shows no public image for this project.

## How publishing is meant to work

- A GitHub workflow, `.github/workflows/publish.yml`,
  publishes everything when a GitHub Release is published.
  It can also be run by hand from the Actions page
  (workflow_dispatch) — that is how the first publication
  happens, because the v0.2.0 release already exists and
  will not trigger it again.
- **PyPI uses trusted publishing.** There is no PyPI API
  token and no password stored in this repository, and none
  must ever be added. Instead, PyPI is told in advance which
  repository and which workflow file may publish the
  `sreagent` project, and GitHub proves the workflow's
  identity with a short-lived OpenID Connect token
  (`id-token: write`) when it runs.
- **GHCR uses the workflow's own GitHub token.** The job
  logs in to `ghcr.io` with the `GITHUB_TOKEN` GitHub gives
  that one run (`packages: write`), so there is no separate
  registry account or stored secret either.
- The image published is the public-safe hosted demo —
  `ghcr.io/kakoozajerry/sre-agent-demo`, built from
  `Dockerfile.hosted`, the build that refuses to start
  without a token. (GHCR image names are lowercase, so the
  owner's name appears as `kakoozajerry` there.) The plain
  local server image is not published: it is allowed to run
  with no token, which is right on one person's machine and
  wrong for a public image.

## Step 1 — tell PyPI who may publish (one time only)

On pypi.org, signed in as the maintainer:

1. Open your account's publishing settings (Manage account,
   then Publishing) and add a **pending publisher** — pending
   because the project does not exist on PyPI yet.
2. Fill it in with exactly these values. A different
   workflow filename or environment name makes the first
   publish fail, because PyPI checks them word for word:
   - PyPI Project Name: `sreagent`
   - Owner: `KakoozaJerry`
   - Repository name: `sre-agent`
   - Workflow filename: `publish.yml`
   - Environment name: `pypi`

## Step 2 — place the workflow file on GitHub (one time only)

Workflow files cannot be uploaded through the connection
this project is built with — GitHub refused the CI workflow
the same way in Phase 11, and Abraham placed that file on
the website himself. The prepared `publish.yml` is kept
with the project, alongside its tests
(`tests/test_publishing.py`); both land together:

1. On the repository website, create the file
   `.github/workflows/publish.yml` with the prepared
   contents, and commit it to `main`.
2. In the same visit, add `tests/test_publishing.py` and
   this guide's companion files if they are not already
   there, so the tests that pin the workflow run against
   the real file.
3. Optional but recommended: in the repository's Settings,
   under Environments, create an environment named `pypi`.
   The workflow already names it; creating it lets the
   maintainer add protection rules, such as requiring his
   own approval before a publish run proceeds.

## Step 3 — run the first publication

1. Check the version first: the workflow publishes whatever
   version `pyproject.toml` and the package carry at the
   chosen ref (0.2.0 today). PyPI never accepts the same
   version twice, so a re-run needs a new version.
2. On the repository's Actions page, choose the Publish
   workflow and run it by hand on `main` (workflow_dispatch).
   For every later release, publishing that release on
   GitHub runs the same workflow on its own.
3. Watch both jobs: the PyPI job builds the wheel and the
   source distribution, checks them with `twine check`,
   and uploads them; the GHCR job builds `Dockerfile.hosted`
   and pushes the image.

## Step 4 — read it back (a publish is only done when it reads back)

Do not take the workflow's green tick as the proof. Check
the registries themselves:

- PyPI: `https://pypi.org/pypi/sreagent/json` answers 200
  (today it answers 404), and the project page
  `https://pypi.org/project/sreagent/` shows version 0.2.0
  with the metadata from `pyproject.toml`.
- A clean machine (or a fresh folder) runs
  `pipx install sreagent`, and `sre-agent --version` then
  prints `sre-agent 0.2.0`. The command a user runs is
  still called `sre-agent`; only the package name changed.
- GHCR: the repository's Packages list shows
  `sre-agent-demo`, and `docker pull
  ghcr.io/kakoozajerry/sre-agent-demo:0.2.0` works. A newly
  published GHCR package starts private: in the package's
  settings, set its visibility to public and link it to
  the `sre-agent` repository, or no visitor can pull it.
- Only then is the roadmap's distribution item done, and
  only then may the README's install section drop its
  "not published yet" sentence — the packaging tests pin
  that sentence today, and they change in the same commit
  that the read-back earns.
