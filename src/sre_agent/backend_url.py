"""Backend address rules: where the agent may send a request.

In plain words, every live backend in this project — Prometheus,
Kubernetes, Datadog, Azure Monitor — and the command-line tool's
own server address take a base address from whoever sets the
agent up, and some of them send a credential to that address as
a request header. Finding F-05 in the security review named the
risk: a mistyped or hostile address would receive a live token,
and a plain ``http://`` address would carry it across a network
unencrypted.

This module is the one place that risk is answered, so every
backend applies the same rules:

- **https, or loopback http, and nothing else.** An ``https://``
  address is accepted. A plain ``http://`` address is accepted
  only when its host is on this same machine — ``localhost``,
  ``127.0.0.0/8`` or ``::1`` — which is where the demo backends
  and the local server live. Plain http to any other host is
  refused, and so is every other scheme (``ftp:``, ``file:``,
  a bare host with no scheme, and the rest).
- **No credentials inside the address.** An address carrying a
  user or password (``https://user:secret@host``) is refused:
  credentials belong in the header a backend adds on purpose,
  never pasted into an address that gets logged and copied.
- **An optional allow-list of hosts.** A caller may pass
  ``allowed_hosts`` — host names, optionally with a port, such
  as ``api.datadoghq.com`` or ``prometheus.internal:9090``. When
  a list is given, the address's host (and its port, when the
  entry names one) must be on it. The list can only narrow where
  the agent may call; it can never widen the scheme rule — an
  allow-list entry does not make plain http to a remote host
  acceptable.
- **One explicit exception, named differently on purpose.**
  A caller may pass a separate list, ``allow_http_hosts``,
  naming hosts where plain http is accepted even though they
  are not loopback. This exists for one case: a private
  container network the operator controls, where the service
  speaks only http and its traffic never leaves that network —
  the Docker Compose demo's Prometheus, at
  ``http://prometheus:9090``, is the case this was added for
  (Phase 12). It is a separate parameter, not a widening of
  ``allowed_hosts``, so the rule above still holds for that
  list; naming a host here is the operator's deliberate,
  written-down decision for that one host, it applies to no
  other host, and no credential is sent to the demo's
  Prometheus (it takes none). Addresses still come only from
  operator code or configuration, never from telemetry.

The check runs when a backend is built, before any request is
made, and raises ``ValueError`` with a plain reason. Addresses
still come only from operator code or configuration — never
from telemetry, an alert or a model output — and that boundary,
stated in the review, is unchanged; these rules are the second
layer, for the day an operator's address is wrong.
"""

from __future__ import annotations

import ipaddress
import urllib.parse
from collections.abc import Iterable

_LOOPBACK_NAMES = {"localhost"}


def _is_loopback(host: str) -> bool:
    """True when *host* names this same machine only."""
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host_matches(host: str, port: int | None, entry: str) -> bool:
    """True when one allow-list *entry* names this host (and port).

    An entry is a host name, optionally with ``:port``. A bare
    host entry allows that host on any port; an entry with a
    port allows only that port. Matching is case-insensitive,
    as host names are.
    """
    entry = entry.strip().lower()
    if not entry:
        return False
    if entry.startswith("["):  # a bracketed IPv6 entry, [::1] or [::1]:8443
        end = entry.find("]")
        if end == -1:
            return False
        entry_host = entry[1:end]
        rest = entry[end + 1 :]
        entry_port: int | None = None
        if rest.startswith(":"):
            try:
                entry_port = int(rest[1:])
            except ValueError:
                return False
        elif rest:
            return False
    elif entry.count(":") == 1:
        entry_host, _, port_text = entry.partition(":")
        try:
            entry_port = int(port_text)
        except ValueError:
            return False
    else:
        entry_host, entry_port = entry, None
    if host.lower() != entry_host:
        return False
    return entry_port is None or entry_port == port


def validate_backend_url(
    url: str,
    *,
    allowed_hosts: Iterable[str] | None = None,
    allow_http_hosts: Iterable[str] | None = None,
    what: str = "backend address",
) -> str:
    """Check one base address against the rules; return it cleaned.

    The returned address is the caller's, stripped of surrounding
    whitespace and any trailing slash, so a backend can append
    its paths to it directly. Anything outside the rules raises
    ``ValueError`` with a plain reason naming *what* was checked.
    """
    cleaned = url.strip().rstrip("/") if isinstance(url, str) else ""
    if not cleaned:
        raise ValueError(f"{what} must not be empty")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError(f"{what} must not contain spaces: {url!r}")
    parsed = urllib.parse.urlparse(cleaned)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError(
            f"{what} must be an http:// or https:// address with a host: {url!r}"
        )
    if parsed.username or parsed.password:
        raise ValueError(
            f"{what} must not carry a user or password in the address: {url!r}"
        )
    if parsed.scheme == "http" and not _is_loopback(host):
        # The one explicit exception: a host the operator named
        # in ``allow_http_hosts`` for a private network they
        # control (see this module's docstring). Any other host
        # is refused, exactly as before.
        http_entries = list(allow_http_hosts) if allow_http_hosts else []
        try:
            http_port = parsed.port
        except ValueError:
            http_port = -1
        if not any(
            _host_matches(host, http_port, entry) for entry in http_entries
        ):
            raise ValueError(
                f"{what} uses plain http to a host that is not on this "
                f"machine ({host}); use https:// for any address beyond "
                "localhost, so a credential cannot cross a network "
                "unencrypted"
            )
    if allowed_hosts is not None:
        entries = list(allowed_hosts)
        try:
            port = parsed.port
        except ValueError:
            port = -1  # an unparseable port matches no entry
        if not any(_host_matches(host, port, entry) for entry in entries):
            raise ValueError(
                f"{what} host {host} is not on the allow-list "
                f"for this backend: {sorted(entries)}"
            )
    return cleaned
