"""Whether a request reached this node from the machine it runs on.

A loopback source address is this node's own software or a browser on the
same computer. So is a source address that is the very address of this
machine the connection reached: a client on this machine that connects to
one of the machine's own network addresses, such as one its name resolves
to, connects from that address too. Anything else is a peer or a remote
client. Data statistics count the two separately (Phase 1 Step 8), and the
``/config`` interface serves only local clients (HttpApi §2.3, Step 14).
"""

from __future__ import annotations
from ipaddress import IPv4Address, IPv6Address, ip_address


def is_local_client(address: str, reached: str = "") -> bool:
    """Whether ``address`` is a loopback address, or ``reached``.

    ``reached`` is the address of this machine the client's connection
    reached, if it is known. A client elsewhere cannot connect from it, as
    the replies to it never leave this machine. An IPv4-mapped IPv6 address
    such as ``::ffff:127.0.0.1`` is unwrapped first, since a dual-stack
    listener reports IPv4 clients that way. An address that cannot be parsed
    at all is not treated as local.
    """
    parsed = parsed_address(address)

    if parsed is None:
        return False

    return parsed.is_loopback or parsed == parsed_address(reached)


def parsed_address(address: str) -> IPv4Address | IPv6Address | None:
    """``address`` parsed, unwrapped if it is IPv4-mapped, or ``None`` if it is no IP address."""
    try:
        parsed = ip_address(address)

    except ValueError:
        # Not logged: an address that is not an IP is not a local client.
        return None

    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped

    return parsed
