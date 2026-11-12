"""Fail closed on network attempts, including attempts swallowed by dependencies."""

from contextlib import ExitStack, contextmanager
from unittest.mock import patch


@contextmanager
def deny_network():
    attempts = []

    def denied(*args, **kwargs):
        attempts.append("network attempt")
        raise AssertionError("Network is forbidden during retrieval")

    with ExitStack() as stack:
        for target in (
            "socket.getaddrinfo",
            "socket.create_connection",
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.socket.sendto",
            "socket.socket.sendmsg",
        ):
            stack.enter_context(patch(target, denied))
        yield attempts
        if attempts:
            raise AssertionError(f"{len(attempts)} network attempts, possibly swallowed")
