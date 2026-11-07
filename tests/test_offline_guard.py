"""Verify the CI guard without sending even a single external request."""

import os
import subprocess
import sys
from pathlib import Path


def test_offline_guard_covers_new_interpreters(tmp_path: Path) -> None:
    guard = Path(__file__).parent / "offline"
    environment = os.environ | {
        "EDGE_TEST_OFFLINE": "1",
        "PYTHONPATH": str(guard) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    script = """
import socket
assert socket.socket.connect.__name__ == '_offline_connect'
assert socket.socket.connect_ex.__name__ == '_offline_connect_ex'
assert socket.socket.sendto.__name__ == '_offline_sendto'
calls = [
    lambda: socket.getaddrinfo('example.test', 443),
    lambda: socket.gethostbyname('example.test'),
    lambda: socket.gethostbyname_ex('example.test'),
    lambda: socket.gethostbyaddr('192.0.2.1'),
    lambda: socket.getnameinfo(('192.0.2.1', 443), 0),
    lambda: socket.socket().connect(('192.0.2.1', 443)),
    lambda: socket.socket().connect_ex(('192.0.2.1', 443)),
    lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b'x', ('192.0.2.1', 53)),
    lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b'x', 0, ('example.test', 53)),
]
if hasattr(socket.socket, 'sendmsg'):
    assert socket.socket.sendmsg.__name__ == '_offline_sendmsg'
    calls.append(lambda: socket.socket(type=socket.SOCK_DGRAM).sendmsg(
        [b'x'], [], 0, ('192.0.2.1', 53)))
for call in calls:
    try:
        call()
    except OSError as error:
        assert 'Offline tests prohibit' in str(error)
    else:
        raise AssertionError('Network guard missing')
# Windows/Streamlit internals can resolve loopback without an external lookup.
assert socket.getaddrinfo('127.0.0.1', 80)
assert socket.gethostbyname('127.0.0.1') == '127.0.0.1'
assert socket.gethostbyname_ex('127.0.0.1')[2] == ['127.0.0.1']
# Actual UDP is loopback-only, proving allowed variants still delegate normally.
with socket.socket(type=socket.SOCK_DGRAM) as receiver:
    receiver.bind(('127.0.0.1', 0))
    receiver.settimeout(2)
    with socket.socket(type=socket.SOCK_DGRAM) as sender:
        sender.sendto(b'allowed', receiver.getsockname())
    assert receiver.recvfrom(1024)[0] == b'allowed'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
