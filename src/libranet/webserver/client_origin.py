"""Whether a request reached this node from the machine it runs on.

A loopback source address is this node's own software or a browser on the
same computer; anything else is a peer or a remote client. Data statistics
count the two separately (Phase 1 Step 8), and the ``/config`` interface
serves only local clients (HttpApi §2.3, Step 14).
"""

from __future__ import annotations
from ipaddress import IPv6Address, ip_address


def is_local_client(address: str) -> bool:
    """Whether ``address`` is a loopback address.

    An IPv4-mapped IPv6 address such as ``::ffff:127.0.0.1`` is unwrapped
    first, since a dual-stack listener reports IPv4 clients that way. An
    address that cannot be parsed at all is not treated as local.
    """
    try:
        parsed = ip_address(address)

    except ValueError:
        return False

    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped.is_loopback

    return parsed.is_loopback
