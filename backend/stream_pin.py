"""Run one chat stream on a single thread.

Starlette pulls a sync body with a fresh worker per chunk, and each worker
starts from the event-loop context. The request context holds the fold
scheduler, so hopping threads drops it, and a reused worker can still be
holding another chat's copy. The reader may hop. The producer that walks the
turn does not.
"""

from __future__ import annotations

import contextvars
import queue
import threading
from collections.abc import Iterator


def pin_stream(source: Iterator[bytes]) -> Iterator[bytes]:
    """Yield `source` from one thread that inherits the caller's context."""
    ctx = contextvars.copy_context()
    incoming: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=8)
    stop = threading.Event()

    def offer(item: bytes | BaseException | None) -> bool:
        while not stop.is_set():
            try:
                incoming.put(item, timeout=0.2)
                return True
            except queue.Full:
                continue
        return False

    def produce() -> None:
        def run() -> None:
            try:
                for chunk in source:
                    if not offer(chunk):
                        break
                else:
                    offer(None)
            except BaseException as err:
                if stop.is_set() or isinstance(err, GeneratorExit):
                    return
                offer(err)
            finally:
                source.close()

        ctx.run(run)

    def chunks() -> Iterator[bytes]:
        threading.Thread(target=produce, name="chat-stream", daemon=True).start()
        try:
            while True:
                item = incoming.get()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            stop.set()

    return chunks()
