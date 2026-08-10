"""URL security checks (SSRF prevention).

Guards the download engine against Server-Side Request Forgery: it blocks
private/loopback/link-local/reserved IP ranges, dangerous hostnames, and
rejects schemes other than HTTP/HTTPS.
"""

from __future__ import annotations

__all__ = ["validate_download_url", "resolve_host"]

import ipaddress
import socket
from typing import Tuple
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        "metadata.google.internal",          # cloud metadata endpoint
        "169.254.169.254",                  # AWS/GCP/Azure metadata IP literal
    }
)

# Schemes that may appear in user input but are categorically unsafe here.
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if the address is private, loopback, link-local, or otherwise unsafe."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _extract_host(hostname: str) -> str:
    host = hostname.strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def resolve_host(hostname: str) -> Tuple[bool, str]:
    """Resolve hostname and reject private/sensitive addresses.

    **This function performs a blocking DNS lookup via ``socket.getaddrinfo``.**
    It must only be called from worker threads -- never from the tkinter main
    thread — to avoid UI freezes.  In ``validate_download_url`` it is only
    reached when ``block_private=True``; the caller's early-return guards the
    fast path.

    Returns ``(True, "")`` when every resolved address is public, otherwise
    ``(False, reason)``.  A hostname is also rejected when DNS cannot resolve
    it, since a download cannot proceed anyway.
    """
    host = _extract_host(hostname)
    if host in _BLOCKED_HOSTNAMES:
        return False, "Blocked hostname"

    # Literal IP address.
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            return False, "Blocked IP address"
        return True, ""
    except ValueError:
        pass

    # Hostname: resolve and inspect all returned addresses.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, "Cannot resolve hostname"

    if not infos:
        return False, "Hostname did not resolve to any address"

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            return False, f"Hostname resolves to blocked address: {addr}"

    return True, ""


def validate_download_url(url: str, block_private: bool = True) -> Tuple[bool, str]:
    """Validate URL scheme and optionally block SSRF targets.

    Always enforces an http(s) scheme and the presence of a host.  When
    ``block_private`` is set (the default), also resolves the host and rejects
    private/loopback/link-local/reserved addresses.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL"

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, "URL must use http or https"
    if not parsed.netloc:
        return False, "URL missing host"

    # Reject credentials embedded in the URL (user:pass@host) — they are a
    # well-known obfuscation vector for phishing-style links and we support
    # auth explicitly via config instead.
    if parsed.username or parsed.password:
        return False, "Embedded URL credentials are not allowed"

    if not block_private:
        return True, ""

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "URL missing host"
    return resolve_host(hostname)
