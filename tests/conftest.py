"""Public tests must use in-process transports or loopback fixtures only."""

import ipaddress
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "YOUTUBE_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    original = socket.socket.connect
    original_ex = socket.socket.connect_ex

    def allowed(address):
        if isinstance(address, tuple):
            host = address[0]
            if host == "localhost":
                return
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            raise AssertionError("external network prohibited in tests")

    def connect(sock, address):
        allowed(address)
        return original(sock, address)

    def connect_ex(sock, address):
        allowed(address)
        return original_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
