import threading
import time


class Waiter:
    def __init__(self):
        self.event = threading.Event()
        self.reply = None
        self.send_ts = time.perf_counter()
        self.latency_ms = None

    def complete(self, reply):
        recv_ts = time.perf_counter()
        self.reply = reply
        self.latency_ms = (recv_ts - self.send_ts) * 1000.0
        self.event.set()

    def wait(self, timeout=None):
        ok = self.event.wait(timeout=timeout)
        if not ok:
            return None
        return self.reply


class WaiterTable:
    def __init__(self):
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._next_msg_id = 1
        self._msg_id_lock = threading.Lock()

    def alloc_msg_id(self):
        with self._msg_id_lock:
            msg_id = self._next_msg_id
            self._next_msg_id += 1
            return msg_id

    def create(self, msg_id=None):
        if msg_id is None:
            msg_id = self.alloc_msg_id()

        waiter = Waiter()
        with self._pending_lock:
            self._pending[msg_id] = waiter
        return msg_id, waiter

    def get(self, msg_id):
        with self._pending_lock:
            return self._pending.get(msg_id)

    def complete(self, msg_id, reply):
        waiter = self.get(msg_id)
        if waiter is None:
            return False
        waiter.complete(reply)
        return True

    def remove(self, msg_id):
        with self._pending_lock:
            return self._pending.pop(msg_id, None)