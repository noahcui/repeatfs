import time
import threading
import statistics
from dataclasses import dataclass

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeerClient
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply


@dataclass
class PendingRequest:
    event: threading.Event
    send_ts: float
    reply: object = None
    latency_ms: float = None


class ConcurrentTimestampBenchmarker:
    def __init__(
        self,
        server_id,
        client_id,
        server_host="127.0.0.1",
        server_port=9000,
        local_host="127.0.0.1",
        local_port=9001,
        total_requests=1000,
        concurrency=4,
        timeout_s=5.0,
        warmup_requests=20,
    ):
        self.server_id = server_id
        self.client_id = client_id
        self.server_host = server_host
        self.server_port = server_port
        self.local_host = local_host
        self.local_port = local_port
        self.total_requests = total_requests
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.warmup_requests = warmup_requests

        self.cfg = Config()
        self.q = msgQueue()
        self.reg = MessagesRegister()

        self.client = BasicTCPPeerClient(
            id=self.client_id,
            local_host=self.local_host,
            local_port=self.local_port,
            msgQ=self.q,
            message_register=self.reg,
            cfg=self.cfg,
        )

        self.client.register_message(TimestampReply, self.handle_timestamp_reply)

        self.pending = {}
        self.pending_lock = threading.Lock()

        self.msg_id_lock = threading.Lock()
        self.next_msg_id = 1

        self.results_lock = threading.Lock()
        self.latencies_ms = []
        self.success = 0
        self.timeouts = 0
        self.errors = 0

    def alloc_msg_id(self):
        with self.msg_id_lock:
            msg_id = self.next_msg_id
            self.next_msg_id += 1
            return msg_id

    def handle_timestamp_reply(self, netman, msg):
        recv_ts = time.perf_counter()

        with self.pending_lock:
            pending = self.pending.get(msg.msg_id)

        if pending is None:
            return

        pending.reply = msg
        pending.latency_ms = (recv_ts - pending.send_ts) * 1000.0
        pending.event.set()

    def connect(self):
        ok = self.client.connect_to_peer(self.server_host, self.server_port)
        if not ok:
            print("[bench] failed to connect")
            return False

        print(f"[bench] connected to {self.server_host}:{self.server_port}")
        time.sleep(1)
        return True

    def send_one(self, msg_id, record_result=True):
        req = TimestampRequest(
            cid=self.client_id,
            sid=self.server_id,
            msg_id=msg_id,
        )

        pending = PendingRequest(
            event=threading.Event(),
            send_ts=time.perf_counter(),
        )

        with self.pending_lock:
            self.pending[msg_id] = pending

        try:
            self.client.send(self.server_id, req)
            ok = pending.event.wait(timeout=self.timeout_s)

            if not ok:
                if record_result:
                    with self.results_lock:
                        self.timeouts += 1
                return False

            if record_result:
                with self.results_lock:
                    self.success += 1
                    self.latencies_ms.append(pending.latency_ms)
            return True

        except Exception:
            if record_result:
                with self.results_lock:
                    self.errors += 1
            return False

        finally:
            with self.pending_lock:
                self.pending.pop(msg_id, None)

    def warmup(self):
        for _ in range(self.warmup_requests):
            msg_id = -self.alloc_msg_id()
            self.send_one(msg_id, record_result=False)

    def worker(self, request_count):
        for _ in range(request_count):
            msg_id = self.alloc_msg_id()
            self.send_one(msg_id, record_result=True)

    def percentile(self, data, p):
        if not data:
            return None
        data = sorted(data)
        if len(data) == 1:
            return data[0]
        k = (len(data) - 1) * (p / 100.0)
        f = int(k)
        c = min(f + 1, len(data) - 1)
        if f == c:
            return data[f]
        return data[f] + (data[c] - data[f]) * (k - f)

    def print_summary(self, elapsed_s):
        total = self.total_requests
        success = self.success
        timeouts = self.timeouts
        errors = self.errors
        failures = total - success - timeouts - errors

        print("\n========== Concurrent Benchmark Summary ==========")
        print(f"total requests : {total}")
        print(f"concurrency    : {self.concurrency}")
        print(f"success        : {success}")
        print(f"timeouts       : {timeouts}")
        print(f"errors         : {errors}")
        print(f"other failures : {failures}")
        print(f"elapsed        : {elapsed_s:.6f} s")

        if success > 0:
            lats = self.latencies_ms
            print(f"throughput     : {success / elapsed_s:.2f} req/s")
            print(f"min latency    : {min(lats):.3f} ms")
            print(f"max latency    : {max(lats):.3f} ms")
            print(f"avg latency    : {sum(lats) / len(lats):.3f} ms")
            print(f"median latency : {statistics.median(lats):.3f} ms")
            print(f"p95 latency    : {self.percentile(lats, 95):.3f} ms")
            print(f"p99 latency    : {self.percentile(lats, 99):.3f} ms")
        else:
            print("no successful replies")

        print("==================================================\n")

    def run(self):
        if not self.connect():
            return

        print(
            f"[bench] warmup={self.warmup_requests}, total={self.total_requests}, "
            f"concurrency={self.concurrency}, timeout={self.timeout_s}s"
        )

        self.warmup()

        base = self.total_requests // self.concurrency
        extra = self.total_requests % self.concurrency

        threads = []
        start = time.perf_counter()

        for i in range(self.concurrency):
            cnt = base + (1 if i < extra else 0)
            if cnt == 0:
                continue
            t = threading.Thread(target=self.worker, args=(cnt,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        end = time.perf_counter()

        self.print_summary(end - start)
        self.client.close()


def main():
    bench = ConcurrentTimestampBenchmarker(
        server_id=ID(0, 0),
        client_id=ID(1, 1, True),
        server_host="127.0.0.1",
        server_port=9000,
        local_host="127.0.0.1",
        local_port=9001,
        total_requests=1000,
        concurrency=8,
        timeout_s=5.0,
        warmup_requests=20,
    )
    bench.run()


if __name__ == "__main__":
    main()