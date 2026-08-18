"""Network process preferences shared by HTTP adapters."""

from __future__ import annotations


def apply_ipv4_preference(force: bool = False) -> None:
    """Prefer IPv4 for unspecified DNS resolution when explicitly enabled."""
    if not force:
        return
    import socket

    if getattr(socket.getaddrinfo, "_VoidCube_ipv4_patched", False):
        return
    original_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == 0:
            try:
                return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
            except socket.gaierror:
                return original_getaddrinfo(host, port, family, type, proto, flags)
        return original_getaddrinfo(host, port, family, type, proto, flags)

    ipv4_getaddrinfo._VoidCube_ipv4_patched = True  # type: ignore[attr-defined]
    socket.getaddrinfo = ipv4_getaddrinfo  # type: ignore[assignment]


__all__ = ["apply_ipv4_preference"]
