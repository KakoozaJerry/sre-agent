# The demo environment — in plain English

The demo is a small, fake production system that runs on your own
machine. Its job is to break on purpose, so you can watch the agent
notice, diagnose the incident, and — with a person's approval —
run a fix, without anything real being at risk.

## What runs

Four containers, started together by `docker-compose.yml` in the
top folder of this repository:

- **sample-service** — a pretend checkout service. It counts every
  request it serves, and it has a switch that makes those requests
  start failing. It publishes its counts (requests and errors) in
  the format Prometheus reads. The code is `demo/sample_service/`
  and it uses only the Python standard library.
- **prometheus** — the monitoring tool. Every few seconds it reads
  the sample service's counts. One alert rule, `HighErrorRate`,
  fires when the error count is above zero. The rule uses the same
  metric name the agent's reasoning loop asks about, and the same
  labels the sample service publishes, so the alert, the incident,
  and the diagnosis all line up.
- **alertmanager** — Prometheus's messenger. When the alert fires
  it sends it to the agent's webhook, and when the failure stops
  it sends a "resolved" message too, so the agent can close the
  incident.
- **sre-agent** — this project's webhook server, run in the
  Compose demo through its live factory
  (`sre_agent.demo_live:create_live_demo_app`). It turns the
  alert into an incident and diagnoses it at once, and it
  serves the approval flow for fixes. Its diagnosis reads the
  demo's own live Prometheus for its metrics and its service
  status, and the sample service's own recorded log lines for
  its logs — so the diagnosis is of the fault
  `./demo/inject_fault.sh` actually injected, not of a seeded
  picture. (The shipped app outside Compose,
  `sre_agent.server:app`, is a different wiring: it still
  reads the seeded demo data, and the README walkthrough that
  uses it says so. The hosted demo is unchanged too — seeded
  and Tier-3-only by design.)

## Run it

You need Docker with the Compose plugin. Then, in the top folder:

```bash
docker compose up --build
```

In a second terminal, start the failure and make some failing
requests:

```bash
./demo/inject_fault.sh
```

(If the script will not run directly after a fresh clone, use
`bash demo/inject_fault.sh` — it does the same thing.)

Within about 15 seconds the alert fires, and the incident lands —
already diagnosed. Watch it arrive:

```bash
curl http://localhost:8080/incidents
```

## A visitor's whole path

1. **Send the demo alert.** `./demo/inject_fault.sh` (above) does
   this the real way, through Prometheus and Alertmanager. The
   incident it opens is diagnosed at once, from the live
   Prometheus numbers and the sample service's live log lines:
   the error count in the evidence is the count Prometheus
   scraped from the failing service, not a seeded number.
2. **Read the diagnosis.** `curl http://localhost:8080/incidents`
   lists it; `curl http://localhost:8080/incidents/<id>` shows the
   diagnosis and the evidence behind it. (This live wiring
   carries no seeded past-incident advice — advice in this
   mode would have to be live too, or not present.)
3. **Propose the restart, and approve it by name.** The shipped
   server runs under a Tier 2 policy, so a fix waits for a person:

   ```bash
   curl -X POST http://localhost:8080/incidents/<id>/actions \
     -H 'Content-Type: application/json' \
     -d '{"runbook_id":"restart-workload"}'
   ```

   The reply names a pending approval. Approve that exact action
   under your own name:

   ```bash
   curl -X POST \
     http://localhost:8080/incidents/<id>/approvals/<pending-id>/approve \
     -H 'Content-Type: application/json' \
     -d '{"by":"Your Name"}'
   ```

   The restart runs — against the seeded demo workloads, not the
   sample container — and the incident moves to `mitigating`,
   never straight to `resolved`: running a fix is not proof it
   worked.
4. **Close the incident.** Stop the failure:

   ```bash
   ./demo/inject_fault.sh off
   ```

   The resolved alert closes the incident. To stop everything:
   `docker compose down`.

Useful pages while it runs: the sample service answers at
http://localhost:8001/ (try `/work`, `/metrics`, `/chaos`),
Prometheus at http://localhost:9090, and Alertmanager at
http://localhost:9093.

## How the demo images are kept tidy

Two of this project's own containers — the agent and the sample
service — run as a named user who is not root, and the agent
image installs its Python packages from `requirements.lock` in
the top folder: exact versions, with a hash for every package,
checked as it installs, so the bytes that run are the bytes that
were locked. One honest remainder, from the security review
(finding F-06): Prometheus and Alertmanager are still pinned in
`docker-compose.yml` by their version tags, not by image digest,
because no verified digest could be produced where this project
is built — an invented digest would be worse than the tag.

## What the demo does not do — honestly

- A fix runs against the seeded demo workloads, not against the
  sample-service container: approving the restart changes the
  demo workload records the agent keeps, and the sample service
  stops failing only when you switch its chaos switch off.
- This server has no token set, so it is unauthenticated — a
  local demo for one machine, not a public server. The
  public-safe build is the hosted demo mode in
  [hosted-demo.md](hosted-demo.md).

## How it is checked

The sample service is plain Python, so the test suite starts its
real HTTP routes and exercises them: request and error counts, the
chaos switch, and the metrics text. The test suite also checks
that the Compose, Prometheus, and Alertmanager files agree with
each other and with the agent's code — service names, ports, the
webhook address, and the metric names — and it runs the whole
visitor path above against the shipped server, using the demo
files' own alert labels and summary: alert, diagnosis, a restart
approved by name, and the resolved alert closing the incident.

The live wiring is checked the same honest way, in
`tests/test_demo_live.py`: a fake Prometheus answering in the
real Prometheus reply shape with values the test chooses, and
the real sample service driven the way `inject_fault.sh` drives
it. With the failure on, the live diagnosis names that fault
and its metric evidence carries the live count (3, in the
test) — not the seeded layer's 162; with no failure recorded,
the same live layer says "I don't know", where the seeded
layer would always diagnose its made-up picture. The Compose
file's live factory and its two service addresses are pinned
as text, like the rest of the wiring.

A full `docker compose up` run needs Docker, and there is
no Docker in the build environment where this project is
written, so that run stays a human-run check on top of the
automatic ones, not something the tests pretend to do. The visitor path
was also run end to end from a clean checkout on 12 November
2026 — the stack's own pieces over real HTTP (the sample
service, the shipped server, and the exact Alertmanager payload
the demo config sends) — with every step above answering as
described.
