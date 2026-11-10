"""Backend address rules (finding F-05).

Every live backend — and the CLI's server address — sends, or
can send, a credential to the address it is given. These tests
pin the rules in ``backend_url.py``: https for anything beyond
this machine, plain http only to loopback, no credentials inside
the address, and an optional host allow-list that can narrow
where the agent may call but never widen the https rule. The
backends are only constructed here; no network call is made.
"""

from __future__ import annotations

import pytest

from sre_agent.adapters import (
    AzureLogAnalyticsBackend,
    DatadogMetricsBackend,
    KubernetesApiBackend,
)
from sre_agent.backend_url import validate_backend_url
from sre_agent.tools import PrometheusMetricsBackend


def test_https_to_a_remote_host_is_accepted() -> None:
    assert (
        validate_backend_url("https://metrics.example.com:9090/")
        == "https://metrics.example.com:9090"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9090",
        "http://127.10.20.30:9090",
        "http://localhost:9090",
        "http://[::1]:9090",
    ],
)
def test_plain_http_to_this_machine_is_accepted(url: str) -> None:
    assert validate_backend_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://metrics.example.com:9090",
        "http://10.0.0.5:9090",
        "http://192.168.1.20:9090",
    ],
)
def test_plain_http_beyond_this_machine_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="plain http"):
        validate_backend_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://metrics.example.com",
        "file:///etc/passwd",
        "metrics.example.com:9090",
        "https://",
        "",
        "   ",
    ],
)
def test_other_schemes_and_hostless_addresses_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        validate_backend_url(url)


def test_an_address_carrying_credentials_is_refused() -> None:
    with pytest.raises(ValueError, match="must not carry"):
        validate_backend_url("https://operator:hunter2@metrics.example.com")


def test_allow_list_accepts_a_listed_host_and_refuses_an_unlisted_one() -> None:
    allowed = ["api.datadoghq.com"]
    assert (
        validate_backend_url("https://api.datadoghq.com", allowed_hosts=allowed)
        == "https://api.datadoghq.com"
    )
    with pytest.raises(ValueError, match="not on the allow-list"):
        validate_backend_url("https://api.example.com", allowed_hosts=allowed)


def test_allow_list_entry_with_a_port_pins_the_port() -> None:
    allowed = ["prometheus.internal:9090"]
    assert validate_backend_url(
        "https://prometheus.internal:9090", allowed_hosts=allowed
    )
    with pytest.raises(ValueError, match="not on the allow-list"):
        validate_backend_url("https://prometheus.internal:8080", allowed_hosts=allowed)


def test_allow_list_cannot_widen_the_https_rule() -> None:
    # Listing a remote host does not make plain http to it acceptable.
    with pytest.raises(ValueError, match="plain http"):
        validate_backend_url(
            "http://metrics.example.com:9090",
            allowed_hosts=["metrics.example.com"],
        )


def test_every_live_backend_checks_its_address_at_build_time() -> None:
    with pytest.raises(ValueError):
        PrometheusMetricsBackend("http://metrics.example.com:9090")
    with pytest.raises(ValueError):
        KubernetesApiBackend("http://cluster.example.com:6443", token="t")
    with pytest.raises(ValueError):
        DatadogMetricsBackend("k", "k", base_url="http://api.example.com")
    with pytest.raises(ValueError):
        AzureLogAnalyticsBackend("ws", "t", base_url="http://api.example.com")


def test_every_live_backend_accepts_https_and_loopback_http() -> None:
    PrometheusMetricsBackend("https://metrics.example.com:9090")
    PrometheusMetricsBackend("http://127.0.0.1:9090")
    KubernetesApiBackend("https://cluster.example.com:6443", token="t")
    DatadogMetricsBackend("k", "k")  # the default is the real https API host
    AzureLogAnalyticsBackend("ws", "t")  # the default is the real https API host
