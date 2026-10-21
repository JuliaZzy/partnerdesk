"""The chat body stays on one thread, and leaving it closes that turn."""

from __future__ import annotations

import threading
from contextvars import ContextVar

from backend.stream_pin import pin_stream

_probe: ContextVar[int] = ContextVar("pin_probe", default=0)


def test_pinned_stream_keeps_the_caller_context_and_leaves_it_unchanged():
    _probe.set(4)
    seen: list[int] = []

    def source():
        seen.append(_probe.get())
        _probe.set(9)
        yield b"a"
        seen.append(_probe.get())
        yield b"b"

    assert b"".join(pin_stream(source())) == b"ab"
    assert seen == [4, 9]
    assert _probe.get() == 4


def test_leaving_the_stream_closes_the_source():
    finished = threading.Event()

    def source():
        try:
            for n in range(100):
                yield str(n).encode()
        finally:
            finished.set()

    stream = pin_stream(source())
    assert next(stream) == b"0"
    stream.close()
    assert finished.wait(2)
