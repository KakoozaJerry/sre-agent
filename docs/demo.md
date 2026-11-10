# The demo environment — in plain English

The demo is a small, fake production system that runs on your own
machine. Its job is to break on purpose, so you can watch the agent
notice, record the incident, and (in later phases) diagnose it —
without anything real being at risk.

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
- **sre-agent** — this project's webhook server. It turns the
  alert into an incident, exactly as described in
  [how-it-works.md](how-it-works.md).

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

Within about 15 seconds the alert fires, and the incident lands.
Watch it arrive:

```bash
curl http://localhost:8080/incidents
```

Stop the failure:

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

## What the demo does not do yet — honestly

- The alert-to-incident path is live in the demo. The reasoning
  loop is **not** wired to the demo's live Prometheus yet: its
  tools still read the built-in demo backends, so running a
  diagnosis against the live demo numbers is a later step. The
  seeded demo diagnosis (`demo_diagnosis()`) shows the report
  shape today.
- Nothing in the demo can be fixed by the agent yet. Acting, with
  human approval, is Phase 2.

## How it is checked

The sample service is plain Python, so the test suite starts its
real HTTP routes and exercises them: request and error counts, the
chaos switch, and the metrics text. The test suite also checks
that the Compose, Prometheus, and Alertmanager files agree with
each other and with the agent's code — service names, ports, the
webhook address, and the metric names. A full `docker compose up`
run needs Docker, so that end-to-end run is a human-run check on
top of the automatic ones, not something the tests pretend to do.
