"""Bounded independent file work; callers retain transaction/commit ownership."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def bounded_map(function, items, cancel_event, *, workers=4):
    workers = max(1, min(4, int(workers)))
    if workers == 1:
        for item in items:
            if cancel_event.is_set():
                raise InterruptedError("File preparation cancelled")
            yield function(item)
        return
    iterator = iter(items)
    pending = set()
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="coating-files")
    try:
        exhausted = False
        while pending or not exhausted:
            if cancel_event.is_set():
                raise InterruptedError("File preparation cancelled")
            while not exhausted and len(pending) < workers * 2:
                try:
                    item = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                pending.add(pool.submit(function, item))
            if pending:
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
    finally:
        for future in pending:
            future.cancel()
        # Never let transaction cleanup race a still-writing worker.
        pool.shutdown(wait=True, cancel_futures=True)


class LinkedCancellation:
    """Stop an owned pipeline task without changing the user's operation outcome."""
    def __init__(self, parent):
        import threading
        self.parent = parent
        self.local = threading.Event()

    def is_set(self):
        return self.parent.is_set() or self.local.is_set()

    def set(self):
        self.local.set()
