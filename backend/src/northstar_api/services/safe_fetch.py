from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx


class UnsafeURL(ValueError):
    pass


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SUPPORTED_CONTENT_TYPES = frozenset(
    {"text/html", "text/plain", "text/markdown", "application/xml", "text/xml"}
)
_IPV4_COMPATIBLE_NETWORK = ipaddress.IPv6Network("::/96")
_NAT64_WELL_KNOWN_NETWORK = ipaddress.IPv6Network("64:ff9b::/96")


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    url: httpx.URL
    hostname: str
    port: int
    addresses: tuple[IPAddress, ...]

    @property
    def host_header(self) -> str:
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        default_port = 443 if self.url.scheme == "https" else 80
        return host if self.port == default_port else f"{host}:{self.port}"


async def _lookup_addresses(hostname: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise UnsafeURL("URL host could not be resolved") from exc
    return tuple(record[4][0] for record in records)


def _is_public_address(address: IPAddress) -> bool:
    def is_public(candidate: IPAddress) -> bool:
        return bool(
            candidate.is_global
            and not candidate.is_private
            and not candidate.is_loopback
            and not candidate.is_link_local
            and not candidate.is_multicast
            and not candidate.is_reserved
            and not candidate.is_unspecified
        )

    if not is_public(address):
        return False
    if not isinstance(address, ipaddress.IPv6Address):
        return True

    # Translation/tunnel forms can be globally classified IPv6 addresses while
    # routing to an embedded private IPv4 destination. Validate every embedded
    # endpoint too, including the well-known NAT64 and legacy compatible forms.
    embedded: list[ipaddress.IPv4Address] = []
    if address.ipv4_mapped is not None:
        embedded.append(address.ipv4_mapped)
    if address.sixtofour is not None:
        embedded.append(address.sixtofour)
    if address.teredo is not None:
        embedded.extend(address.teredo)
    if address in _IPV4_COMPATIBLE_NETWORK or address in _NAT64_WELL_KNOWN_NETWORK:
        embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    return all(is_public(candidate) for candidate in embedded)


async def _resolve_public_target(url: str) -> _ResolvedTarget:
    try:
        target = httpx.URL(url)
    except (httpx.InvalidURL, ValueError) as exc:
        raise UnsafeURL("URL is malformed") from exc
    if target.scheme not in {"http", "https"} or not target.raw_host or target.userinfo:
        raise UnsafeURL("Only public HTTP(S) URLs without credentials are allowed")
    try:
        hostname = target.raw_host.decode("ascii").rstrip(".")
    except UnicodeDecodeError as exc:  # pragma: no cover - HTTPX normally applies IDNA first.
        raise UnsafeURL("URL hostname is invalid") from exc
    if not hostname or "%" in hostname:
        raise UnsafeURL("URL hostname is invalid")
    port = target.port or (443 if target.scheme == "https" else 80)
    raw_addresses = await _lookup_addresses(hostname, port)
    if not raw_addresses:
        raise UnsafeURL("URL host did not resolve to an address")

    addresses: list[IPAddress] = []
    for raw_address in raw_addresses:
        if "%" in raw_address:
            raise UnsafeURL("URL resolves to a non-public network")
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise UnsafeURL("URL host returned an invalid address") from exc
        if not _is_public_address(address):
            raise UnsafeURL("URL resolves to a non-public network")
        if address not in addresses:
            addresses.append(address)
    return _ResolvedTarget(url=target, hostname=hostname, port=port, addresses=tuple(addresses))


async def assert_public_url(url: str) -> None:
    await _resolve_public_target(url)


def _new_transport() -> httpx.AsyncBaseTransport:
    # A fresh transport is used per IP attempt. Besides avoiding DNS, this also
    # prevents TLS connections being reused across different hostnames that
    # happen to resolve to the same address during a redirect chain.
    return httpx.AsyncHTTPTransport(retries=0)


async def _fetch_from_address(
    target: _ResolvedTarget, address: IPAddress, *, max_bytes: int
) -> tuple[str | None, str | None]:
    pinned_url = target.url.copy_with(host=address.compressed)
    extensions: dict[str, object] = {}
    if target.url.scheme == "https":
        # httpcore connects to the IP in pinned_url but uses this original DNS
        # name for both SNI and certificate hostname verification.
        extensions["sni_hostname"] = target.hostname
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=8),
        follow_redirects=False,
        trust_env=False,
        transport=_new_transport(),
    ) as client:
        async with client.stream(
            "GET",
            pinned_url,
            headers={"User-Agent": "NorthstarKnowledgeBot/1.0", "Host": target.host_header},
            extensions=extensions,
        ) as response:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect did not include a location")
                return location, None
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in _SUPPORTED_CONTENT_TYPES:
                raise ValueError("URL returned an unsupported content type")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ValueError("URL content exceeded the configured size limit")
            text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
            return None, html_to_text(text) if content_type == "text/html" else text


async def fetch_public_text(
    url: str, *, max_bytes: int = 3_000_000, max_redirects: int = 5
) -> tuple[str, str]:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if max_redirects < 0:
        raise ValueError("max_redirects cannot be negative")
    current = url
    for redirect_count in range(max_redirects + 1):
        target = await _resolve_public_target(current)
        last_connect_error: httpx.HTTPError | None = None
        for address in target.addresses:
            try:
                location, text = await _fetch_from_address(target, address, max_bytes=max_bytes)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_connect_error = exc
        else:
            assert last_connect_error is not None
            raise last_connect_error

        if location is not None:
            if redirect_count == max_redirects:
                break
            current = urljoin(str(target.url), location)
            continue
        assert text is not None
        return text, current
    raise ValueError("URL exceeded the redirect limit")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.parts.append(data.strip() + " ")


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return "".join(parser.parts).strip()
