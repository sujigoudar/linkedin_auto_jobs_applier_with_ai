"""Track 24: SSRF guard for any URL this codebase's new adapter boundary
fetches that ISN'T the operator-configured feed/connection URL itself --
e.g. a linked-article URL found INSIDE an RSS entry's own content, which
the feed's author (not the operator) controls.

`validate_public_fetch_url` mirrors the same approach the upstream
Agent-Reach project's own `agent_reach/utils/url.py` takes (reject
non-http(s) schemes, reject embedded userinfo, resolve the hostname and
reject any non-globally-routable address via Python's `ipaddress`
module's own `is_global`, reject known metadata/internal hostnames) --
written independently for this codebase (different license, different
module, no code copied) rather than importing or vendoring that
project's module, since this track explicitly does not take Agent Reach
as a dependency (see `app/sources/adapter_contract.py`'s own module
docstring).

This is NOT applied to a connection's own `feed_url`/`article_list_url`
-- those are explicitly operator-configured (see
`app/connection_catalog.py`'s own "this adapter never guesses or scans
arbitrary URLs on the operator's behalf" convention for the pre-existing
`WebsiteSource`) and trusted the same way every other adapter's
operator-configured target already is. It exists for a URL this
codebase did NOT choose -- content embedded inside a fetched feed entry.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

#: Hostnames that resolve (or are commonly aliased) to loopback/internal/
#: cloud-metadata targets even when DNS or `ipaddress.is_global` alone
#: might not catch them (e.g. a hosts-file override in a hostile
#: environment, or before this function even gets to resolving DNS at
#: all) -- checked by exact match or suffix, case-insensitively.
_BLOCKED_HOSTNAME_SUFFIXES = (".local", ".internal", ".lan")
_BLOCKED_HOSTNAMES_EXACT = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "169.254.169.254",
    }
)


class UnsafeFetchUrlError(ValueError):
    """Raised by `validate_public_fetch_url` for any URL this codebase
    refuses to fetch -- never silently skipped or fetched anyway."""


def validate_public_fetch_url(url: str) -> str:
    """Returns `url` unchanged when it's safe to fetch as a public,
    arbitrary (not operator-pre-vetted) URL; raises `UnsafeFetchUrlError`
    otherwise. Checked, in order:

    1. Scheme must be `http` or `https` (rejects `file://`, `ftp://`,
       `gopher://`, ...).
    2. No embedded userinfo (`user:pass@host`) -- a classic SSRF/
       credential-smuggling vector.
    3. Hostname must not be a known metadata/internal name (checked
       BEFORE DNS resolution, since some of these -- `169.254.169.254`
       itself -- are literal addresses, not names needing resolution).
    4. The hostname's resolved address(es) must ALL be globally routable
       (`ipaddress.ip_address(...).is_global`) -- rejects loopback,
       private (RFC1918/ULA), link-local (which also catches the cloud
       metadata address 169.254.169.254 even if step 3's exact-match list
       were ever incomplete), and any other non-public range. A hostname
       that resolves to MULTIPLE addresses (DNS rebinding risk) is
       rejected if ANY of them is non-global -- fail closed, never
       "the first one looked fine"."""
    if not url or not url.strip():
        raise UnsafeFetchUrlError("url is empty")

    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeFetchUrlError(f"unsupported scheme {scheme!r} -- only http/https may be fetched")

    if parts.username is not None or parts.password is not None:
        raise UnsafeFetchUrlError("embedded userinfo (user:pass@host) in a fetch URL is not allowed")

    hostname = parts.hostname
    if not hostname:
        raise UnsafeFetchUrlError("url has no hostname")

    hostname_lower = hostname.lower().rstrip(".")
    if hostname_lower in _BLOCKED_HOSTNAMES_EXACT:
        raise UnsafeFetchUrlError(f"hostname {hostname!r} is a blocked internal/metadata target")
    if any(hostname_lower.endswith(suffix) for suffix in _BLOCKED_HOSTNAME_SUFFIXES):
        raise UnsafeFetchUrlError(f"hostname {hostname!r} uses a blocked internal TLD/suffix")

    # A bare literal IP in the URL resolves to itself; a real hostname
    # needs DNS. Either way, every resulting address must be global.
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeFetchUrlError(f"could not resolve hostname {hostname!r}: {exc}") from exc

    resolved_ips = {str(info[4][0]) for info in addr_infos}
    if not resolved_ips:
        raise UnsafeFetchUrlError(f"hostname {hostname!r} resolved to no addresses")
    for ip_str in resolved_ips:
        # IPv6 link-local addresses can carry a `%scope` suffix (e.g.
        # "fe80::1%eth0") that `ipaddress.ip_address` rejects outright --
        # strip it for parsing; the address itself is still checked
        # below (and a link-local one is non-global regardless).
        ip_str_bare = ip_str.split("%", 1)[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str_bare)
        except ValueError as exc:
            raise UnsafeFetchUrlError(f"hostname {hostname!r} resolved to unparseable address {ip_str!r}") from exc
        if not ip_obj.is_global:
            raise UnsafeFetchUrlError(
                f"hostname {hostname!r} resolves to non-globally-routable address {ip_str!r}"
            )

    return url
