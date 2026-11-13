#!/bin/sh
# One-command install and quickstart for sre-agent (macOS and Linux).
#
# Run it from the top folder of a fresh clone:
#
#     ./quickstart.sh
#
# What it does, in plain words:
#   1. Makes a private Python folder (.venv) in this folder, if
#      there is not one already. Nothing is installed system-wide.
#   2. Installs sre-agent and the web server it needs into that
#      folder, from this source copy.
#   3. Proves the install works: it prints the version, runs one
#      demo diagnosis, and runs the five scored chaos scenarios.
#      If any step fails, the script stops and says so -- it never
#      prints success for a step that did not pass.
#
# You need Python 3.11 or newer on your path (python3 or python).
# Windows users: follow the manual steps in README.md instead;
# they do the same things, one by one.
set -eu

# Work in the folder this script lives in, wherever it was run from.
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "quickstart: no python3 or python found on your path." >&2
    echo "Install Python 3.11 or newer, then run ./quickstart.sh again." >&2
    exit 1
fi

echo "==> Step 1 of 3: private Python folder (.venv)"
if [ ! -x .venv/bin/python ]; then
    "$PYTHON" -m venv .venv
    echo "    Made .venv with $("$PYTHON" --version 2>&1)."
else
    echo "    .venv already exists; keeping it."
fi

echo "==> Step 2 of 3: install sre-agent (server parts included)"
.venv/bin/python -m pip install -e ".[server]"

echo "==> Step 3 of 3: prove it works"
.venv/bin/sre-agent --version
echo "--- Demo diagnosis (seeded demo data, no server needed) ---"
.venv/bin/sre-agent diagnose
echo "--- Five chaos scenarios, scored (must be 5 of 5) ---"
.venv/bin/sre-agent evaluate

cat <<'EOF'

Quickstart done: the install works and the scored checks pass.

Next steps, when you are ready:
  Start the server:  .venv/bin/uvicorn sre_agent.server:app --port 8080
  In another terminal, with the same folder open:
                     .venv/bin/sre-agent incidents
  Full walkthrough (run the demo, send an alert, read a diagnosis,
  approve an action): see the Quick start in README.md.
  Day-to-day running: see docs/operator-guide.md.
EOF
