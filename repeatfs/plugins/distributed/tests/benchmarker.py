import argparse
import threading
import time
import statistics

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeerClient
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply


def percentile(data, p):
    if not data:
        return None

    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)

    if f == c:
        return s[f]

    return s[f] + (s[c] - s[f]) * (k - f)


class TimestampBenchmarker:
    def __init__(self, server_host, server_port, total, concurrency, timeout):
        self.server_host = server_host
        self.server_port = server_port
        self.total = total
        self.concurrency = concurrency
        self.timeout = timeout

        self.client_id = ID(1, 1, True)
        self.server_id = ID(0, 0)

        self.client = None

        self.lock = threading.Lock()
        self.next_msg_id = 1

        self.success = 0
        self.timeouts = 0
        self.errors = 0
        self.mismatches = 0
        self.latencies = []
        self.bad_examples = []

    def setup_client(self):
        cfg = Config()
        q = msgQueue()
        reg = MessagesRegister()

        self.client = BasicTCPPeerClient(
            id=self.client_id,
            local_host="127.0.0.1",
            local_port=self.server_port + 1,
            msgQ=q,
            message_register=reg,
            cfg=cfg,
        )

        self.client.register_message_type(TimestampReply)
        self.client.register_reply_message(TimestampReply)

        ok = self.client.connect_to_peer(self.server_host, self.server_port)
        if not ok:
            raise RuntimeError(f"failed to connect to {self.server_host}:{self.server_port}")

        time.sleep(0.2)

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass

    def alloc_msg_id(self):
        with self.lock:
            msg_id = self.next_msg_id
            self.next_msg_id += 1
            return msg_id

    def same_id(self, a, b):
        return str(a) == str(b)

    def add_bad_example(self, msg_id, reason):
        if len(self.bad_examples) < 10:
            self.bad_examples.append((msg_id, reason))

    def worker(self, nreq):
        for _ in range(nreq):
            msg_id = self.alloc_msg_id()

            req = TimestampRequest(
                cid=self.client_id,
                sid=self.server_id,
                msg_id=msg_id,
            )

            try:
                reply, latency_ms = self.client.send_and_wait_for_reply(
                    self.server_id,
                    req,
                    timeout=self.timeout,
                )
            except Exception as e:
                with self.lock:
                    self.errors += 1
                    self.add_bad_example(msg_id, f"send_error: {repr(e)}")
                continue

            if reply is None:
                with self.lock:
                    self.timeouts += 1
                    self.add_bad_example(msg_id, "timeout")
                continue

            mismatch = []

            if getattr(reply, "msg_id", None) != msg_id:
                mismatch.append(
                    f"msg_id expected={msg_id}, got={getattr(reply, 'msg_id', None)}"
                )

            if not self.same_id(getattr(reply, "cid", None), self.client_id):
                mismatch.append(
                    f"cid expected={self.client_id}, got={getattr(reply, 'cid', None)}"
                )

            if not self.same_id(getattr(reply, "sid", None), self.server_id):
                mismatch.append(
                    f"sid expected={self.server_id}, got={getattr(reply, 'sid', None)}"
                )

            if mismatch:
                with self.lock:
                    self.mismatches += 1
                    self.add_bad_example(msg_id, "; ".join(mismatch))
                continue

            with self.lock:
                self.success += 1
                if latency_ms is not None:
                    self.latencies.append(latency_ms)

    def run(self):
        self.setup_client()

        base = self.total // self.concurrency
        extra = self.total % self.concurrency

        threads = []
        start = time.perf_counter()

        for i in range(self.concurrency):
            nreq = base + (1 if i < extra else 0)

            t = threading.Thread(
                target=self.worker,
                args=(nreq,),
                name=f"bench-worker-{i}",
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        end = time.perf_counter()
        elapsed = end - start

        self.print_report(elapsed)

    def print_report(self, elapsed):
        success_rate = self.success / self.total * 100 if self.total else 0
        throughput = self.success / elapsed if elapsed > 0 else 0

        print("\n========== Timestamp TCP Benchmark ==========")
        print(f"server              : {self.server_host}:{self.server_port}")
        print(f"total requests      : {self.total}")
        print(f"concurrency         : {self.concurrency}")
        print(f"timeout             : {self.timeout:.3f} s")
        print("---------------------------------------------")
        print(f"success             : {self.success}")
        print(f"timeouts            : {self.timeouts}")
        print(f"errors              : {self.errors}")
        print(f"identity mismatches : {self.mismatches}")
        print(f"success rate        : {success_rate:.2f}%")
        print("---------------------------------------------")
        print(f"elapsed             : {elapsed:.6f} s")
        print(f"throughput          : {throughput:.2f} req/s")

        if self.latencies:
            print("---------------------------------------------")
            print(f"latency min         : {min(self.latencies):.3f} ms")
            print(f"latency avg         : {sum(self.latencies) / len(self.latencies):.3f} ms")
            print(f"latency median      : {statistics.median(self.latencies):.3f} ms")
            print(f"latency p95         : {percentile(self.latencies, 95):.3f} ms")
            print(f"latency p99         : {percentile(self.latencies, 99):.3f} ms")
            print(f"latency max         : {max(self.latencies):.3f} ms")

        if self.bad_examples:
            print("---------------------------------------------")
            print("bad examples:")
            for msg_id, reason in self.bad_examples:
                print(f"  msg_id={msg_id}: {reason}")

        print("=============================================\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-host", required=True)
    parser.add_argument("--server-port", type=int, default=9000)
    parser.add_argument("--total", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=5.0)

    args = parser.parse_args()

    bench = TimestampBenchmarker(
        server_host=args.server_host,
        server_port=args.server_port,
        total=args.total,
        concurrency=args.concurrency,
        timeout=args.timeout,
    )

    try:
        bench.run()
    finally:
        bench.close()


if __name__ == "__main__":
    main()