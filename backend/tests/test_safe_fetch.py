from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest

from northstar_api.services import safe_fetch

Lookup = Callable[[str, int], Awaitable[tuple[str, ...]]]


def install_network(
    monkeypatch: pytest.MonkeyPatch,
    lookup: Lookup,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    monkeypatch.setattr(safe_fetch, "_lookup_addresses", lookup)
    monkeypatch.setattr(safe_fetch, "_new_transport", lambda: httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_pins_resolved_ip_and_preserves_tls_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lookups: list[tuple[str, int]] = []
    requests: list[httpx.Request] = []

    async def lookup(hostname: str, port: int) -> tuple[str, ...]:
        lookups.append((hostname, port))
        # A second DNS lookup would simulate rebinding, but the request must use
        # the address returned by this single validation lookup.
        return ("93.184.216.34",) if len(lookups) == 1 else ("127.0.0.1",)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"verified")

    install_network(monkeypatch, lookup, handler)
    text, final_url = await safe_fetch.fetch_public_text("https://knowledge.example:8443/guide?q=rag")

    assert (text, final_url) == ("verified", "https://knowledge.example:8443/guide?q=rag")
    assert lookups == [("knowledge.example", 8443)]
    assert len(requests) == 1
    assert requests[0].url == httpx.URL("https://93.184.216.34:8443/guide?q=rag")
    assert requests[0].headers["host"] == "knowledge.example:8443"
    assert requests[0].extensions["sni_hostname"] == "knowledge.example"


@pytest.mark.asyncio
async def test_redirect_resolves_and_pins_every_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved: list[tuple[str, int]] = []
    requested_hosts: list[str] = []
    address_for_host = {
        "start.example": "93.184.216.34",
        "docs.example": "2606:4700:4700::1111",
    }

    async def lookup(hostname: str, port: int) -> tuple[str, ...]:
        resolved.append((hostname, port))
        return (address_for_host[hostname],)

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "https://docs.example/final"})
        assert request.headers["host"] == "docs.example"
        assert request.extensions["sni_hostname"] == "docs.example"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<p>Safe answer</p><script>ignore()</script>",
        )

    install_network(monkeypatch, lookup, handler)
    text, final_url = await safe_fetch.fetch_public_text("http://start.example/source")

    assert text == "Safe answer"
    assert final_url == "https://docs.example/final"
    assert resolved == [("start.example", 80), ("docs.example", 443)]
    assert requested_hosts == ["93.184.216.34", "2606:4700:4700::1111"]


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_rejected_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    async def lookup(hostname: str, _: int) -> tuple[str, ...]:
        if hostname == "start.example":
            return ("93.184.216.34",)
        return ("169.254.169.254",)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://metadata.internal/latest"})

    install_network(monkeypatch, lookup, handler)
    with pytest.raises(safe_fetch.UnsafeURL, match="non-public"):
        await safe_fetch.fetch_public_text("https://start.example/source")

    assert [request.url.host for request in requests] == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_mixed_public_and_private_dns_result_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_created = False

    async def lookup(_: str, __: int) -> tuple[str, ...]:
        return "93.184.216.34", "10.0.0.7"

    def transport_factory() -> httpx.AsyncBaseTransport:
        nonlocal transport_created
        transport_created = True
        return httpx.MockTransport(lambda _: httpx.Response(500))

    monkeypatch.setattr(safe_fetch, "_lookup_addresses", lookup)
    monkeypatch.setattr(safe_fetch, "_new_transport", transport_factory)

    with pytest.raises(safe_fetch.UnsafeURL, match="non-public"):
        await safe_fetch.fetch_public_text("https://mixed.example/")
    assert not transport_created


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.com/",
        "http://127.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::10.0.0.1]/",
        "http://[64:ff9b::10.0.0.1]/",
    ],
)
async def test_non_http_credentials_and_local_literals_are_rejected(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    async def lookup(hostname: str, _: int) -> tuple[str, ...]:
        return (hostname,)

    monkeypatch.setattr(safe_fetch, "_lookup_addresses", lookup)
    with pytest.raises(safe_fetch.UnsafeURL):
        await safe_fetch.assert_public_url(url)
