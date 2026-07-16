"""URL safety checks for evaluator evidence retrieval (SPEC.md section 21).

Blocks loopback, link-local (including cloud metadata endpoints), private and
otherwise reserved address ranges, both for literal IP hosts and for every
address a hostname resolves to. The resolver is injectable so tests never
touch real DNS.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def default_resolver(host: str) -> list[str]:
    try:
        results = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return sorted({str(entry[4][0]) for entry in results})


def _address_problem(address: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return f"unparseable address {address!r}"
    if not parsed.is_global or parsed.is_multicast:
        return f"address {address} is not a public unicast address"
    return None


def check_url(url: str, resolver: Resolver = default_resolver) -> str | None:
    """Return a rejection reason, or None when the URL is safe to fetch."""
    try:
        parts = urlsplit(url)
    except ValueError as error:
        return f"malformed URL: {error}"
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return f"scheme {parts.scheme!r} is not allowed"
    if not parts.hostname:
        return "URL has no host"
    if parts.username or parts.password:
        return "URLs with embedded credentials are not allowed"
    host = parts.hostname

    try:
        problem = _address_problem(str(ipaddress.ip_address(host)))
    except ValueError:
        # Hostname, not a literal address: every resolved address must be safe.
        addresses = resolver(host)
        if not addresses:
            return f"host {host!r} did not resolve"
        for address in addresses:
            problem = _address_problem(address)
            if problem is not None:
                return problem
        return None
    return problem
