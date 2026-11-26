"""Keep stalled NAS artwork reads out of HTTP request workers."""
from concurrent.futures import Future, TimeoutError
import queue
import threading


class ArtworkReads:
    def __init__(self, workers=2, capacity=4, timeout=0.75):
        self.timeout = timeout
        self.capacity = capacity
        self.lock = threading.Lock()
        self.pending = {}
        self.queue = queue.Queue(maxsize=capacity)
        for index in range(workers):
            threading.Thread(target=self._work, name=f'artwork-read-{index}', daemon=True).start()

    def _work(self):
        while True:
            key, operation, future = self.queue.get()
            try:
                future.set_result(operation())
            except Exception as error:
                future.set_exception(error)
            finally:
                with self.lock:
                    self.pending.pop(key, None)
                self.queue.task_done()

    def read(self, key, operation, fallback):
        with self.lock:
            future = self.pending.get(key)
            if future is None:
                if len(self.pending) >= self.capacity:
                    return fallback
                future = Future()
                self.pending[key] = future
                self.queue.put_nowait((key, operation, future))
        try:
            return future.result(timeout=self.timeout)
        except (TimeoutError, OSError):
            return fallback
