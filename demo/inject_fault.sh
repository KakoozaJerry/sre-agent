#!/usr/bin/env bash
# Inject (or stop) the demo failure.
#
#   ./demo/inject_fault.sh       turn the failure on and make 5 requests
#   ./demo/inject_fault.sh off   turn the failure off (recover)
#
# The sample service lives at SAMPLE_SERVICE_URL, defaulting to the
# port docker-compose publishes on this machine.
set -euo pipefail

BASE="${SAMPLE_SERVICE_URL:-http://localhost:8001}"

case "${1:-on}" in
  on)
    curl -s -X POST "$BASE/chaos/on"
    echo
    for _ in 1 2 3 4 5; do
      curl -s "$BASE/work" >/dev/null || true
    done
    echo "Failure is on: 5 requests made, the error counter is climbing."
    echo "Within about 15 seconds Prometheus fires HighErrorRate and"
    echo "Alertmanager sends it to the agent. Watch it land:"
    echo "  curl http://localhost:8080/incidents"
    ;;
  off)
    curl -s -X POST "$BASE/chaos/off"
    echo
    echo "Failure is off. Alertmanager will send the resolved alert"
    echo "and the agent will close the incident."
    ;;
  *)
    echo "usage: $0 [on|off]" >&2
    exit 2
    ;;
esac
