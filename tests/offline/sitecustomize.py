"""Python socket API guard for offline tests, NOT an OS/network sandbox.

Native code, external programs and deliberately bypassing monkeypatches are not
contained. Never use this guard to execute untrusted code.
"""

import os
import socket


def _loopback(host: object) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


if os.environ.get("EDGE_TEST_OFFLINE") == "1":
    _connect = socket.socket.connect
    _connect_ex = socket.socket.connect_ex
    _getaddrinfo = socket.getaddrinfo
    _gethostbyname = socket.gethostbyname
    _gethostbyname_ex = socket.gethostbyname_ex
    _gethostbyaddr = socket.gethostbyaddr
    _getnameinfo = socket.getnameinfo
    _sendto = socket.socket.sendto

    def _offline_connect(sock, address):
        if not isinstance(address, tuple) or not _loopback(address[0]):
            raise OSError("Offline tests prohibit external network access.")
        return _connect(sock, address)

    def _offline_connect_ex(sock, address):
        if not isinstance(address, tuple) or not _loopback(address[0]):
            raise OSError("Offline tests prohibit external network access.")
        return _connect_ex(sock, address)

    def _offline_getaddrinfo(host, *args, **kwargs):
        if not _loopback(host):
            raise OSError("Offline tests prohibit external DNS access.")
        return _getaddrinfo(host, *args, **kwargs)

    def _guard_dns(lookup):
        def guarded(host, *args, **kwargs):
            if not _loopback(host):
                raise OSError("Offline tests prohibit external DNS access.")
            return lookup(host, *args, **kwargs)

        return guarded

    def _offline_getnameinfo(address, flags):
        if not isinstance(address, tuple) or not _loopback(address[0]):
            raise OSError("Offline tests prohibit external DNS access.")
        return _getnameinfo(address, flags)

    def _offline_sendto(sock, data, *args):
        address = args[-1] if args else None
        if not isinstance(address, tuple) or not _loopback(address[0]):
            raise OSError("Offline tests prohibit external network access.")
        return _sendto(sock, data, *args)

    if hasattr(socket.socket, "sendmsg"):
        _sendmsg = socket.socket.sendmsg

        def _offline_sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
            # With no destination, sendmsg uses an already connected socket.
            if address is not None and (
                not isinstance(address, tuple) or not _loopback(address[0])
            ):
                raise OSError("Offline tests prohibit external network access.")
            return _sendmsg(sock, buffers, ancdata, flags, address)

        socket.socket.sendmsg = _offline_sendmsg

    socket.socket.connect = _offline_connect
    socket.socket.connect_ex = _offline_connect_ex
    socket.socket.sendto = _offline_sendto
    socket.getaddrinfo = _offline_getaddrinfo
    socket.gethostbyname = _guard_dns(_gethostbyname)
    socket.gethostbyname_ex = _guard_dns(_gethostbyname_ex)
    socket.gethostbyaddr = _guard_dns(_gethostbyaddr)
    socket.getnameinfo = _offline_getnameinfo
