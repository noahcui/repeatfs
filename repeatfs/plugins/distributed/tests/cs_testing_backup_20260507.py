import time
import threading
import statistics
import unittest
import random

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeer, BasicTCPPeerClient
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply


def percentile(data, p):
    if not data:
        return None
    s = sorted(data)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


class TimestampTCPTableTest(unittest.TestCase):
    TOTAL_REQUESTS = 500
    CONCURRENCY = 16
    WAIT_TIMEOUT_S = 5.0

    def setup_server(self):
        server_cfg = Config()
        server_q = msgQueue()
        server_reg = MessagesRegister()

        server = BasicTCPPeer(
            id=ID(0, 0),
            local_host=self.SERVER_HOST,
            local_port=self.SERVER_PORT,
            msgQ=server_q,
            message_register=server_reg,
            cfg=server_cfg,
        )

        def server_handler(netman, msg):
            server_ts = time.time()
            with self.audit_lock:
                if msg.msg_id in self.server_seen:
                    self.server_duplicates += 1

                self.server_seen[msg.msg_id] = {
                    "server_ts": server_ts,
                }
                self.server_copy[msg.msg_id] = server_ts

            msg.reply(
                TimestampReply(
                    cid=msg.cid,
                    sid=msg.sid,
                    msg_id=msg.msg_id,
                    time=server_ts,
                )
            )

        server.register_message(TimestampRequest, server_handler)

        server_thread = threading.Thread(
            target=server.listen,
            daemon=True,
            name="tcp-table-test-server-listen",
        )
        server_thread.start()
        return server, server_thread

    def setup_client(self):
        client_cfg = Config()
        client_q = msgQueue()
        client_reg = MessagesRegister()

        client = BasicTCPPeerClient(
            id=ID(1, 1, True),
            local_host=self.CLIENT_HOST,
            local_port=self.CLIENT_PORT,
            msgQ=client_q,
            message_register=client_reg,
            cfg=client_cfg,
        )

        client.register_message_type(TimestampReply)
        client.register_reply_message(TimestampReply)

        ok = client.connect_to_peer(self.SERVER_HOST, self.SERVER_PORT)
        self.assertTrue(ok, "client failed to connect to server")
        time.sleep(0.2)
        return client

    def reset_tables(self, expected_received_count):
        self.recv_event = threading.Event()
        self.expected_received_count = expected_received_count

        self.client_sent = {}
        self.server_seen = {}
        self.client_received = {}

        # 新增：服务端原始发送副本、客户端原始接收副本
        self.server_copy = {}
        self.client_copy = {}

        # 每个 request 的一对一最终结果
        self.request_results = {}

        self.send_errors = 0
        self.server_duplicates = 0
        self.client_duplicates = 0
        self.timeouts = 0
        self.reply_mismatches = 0

    def setUp(self):
        self.SERVER_HOST = "132.177.4.110"
        self.CLIENT_HOST = "127.0.0.1"
        self.SERVER_PORT = 9000
        self.CLIENT_PORT = self.SERVER_PORT + 1

        self.audit_lock = threading.Lock()
        self.reset_tables(expected_received_count=0)

        # self.server, self.server_thread = self.setup_server()
        self.client = self.setup_client()

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass

        try:
            self.server.close()
        except Exception:
            pass

        self.server_thread.join(timeout=1.0)
        time.sleep(1)

    def test3_concurrent_requests_one_to_one(self):
        self.reset_tables(expected_received_count=self.TOTAL_REQUESTS)

        next_msg_id = 1
        msg_id_lock = threading.Lock()
        latencies_ms = []

        def alloc_msg_id():
            nonlocal next_msg_id
            with msg_id_lock:
                msg_id = next_msg_id
                next_msg_id += 1
                return msg_id

        def worker(nreq):
            for _ in range(nreq):
                msg_id = alloc_msg_id()
                send_ts = time.time()
                thread_name = threading.current_thread().name

                with self.audit_lock:
                    self.client_sent[msg_id] = {
                        "client_send_ts": send_ts,
                        "thread": thread_name,
                    }

                req = TimestampRequest(
                    cid=ID(1, 1, True),
                    sid=ID(0, 0),
                    msg_id=msg_id,
                )

                try:
                    reply, latency_ms = self.client.send_and_wait_for_reply(
                        ID(0, 0), req, timeout=self.WAIT_TIMEOUT_S
                    )
                except Exception as e:
                    with self.audit_lock:
                        self.send_errors += 1
                        self.request_results[msg_id] = {
                            "status": "send_error",
                            "exception": repr(e),
                            "request_msg_id": msg_id,
                            "reply_msg_id": None,
                            "expected_server_ts": None,
                            "reply_time": None,
                            "client_send_ts": send_ts,
                            "client_recv_ts": None,
                            "latency_ms": None,
                            "thread": thread_name,
                        }
                        if len(self.request_results) >= self.TOTAL_REQUESTS:
                            self.recv_event.set()
                    continue

                recv_ts = time.time()

                with self.audit_lock:
                    if reply is None:
                        self.timeouts += 1
                        self.request_results[msg_id] = {
                            "status": "timeout",
                            "request_msg_id": msg_id,
                            "reply_msg_id": None,
                            "expected_server_ts": self.server_copy.get(msg_id),
                            "reply_time": None,
                            "client_send_ts": send_ts,
                            "client_recv_ts": None,
                            "latency_ms": None,
                            "thread": thread_name,
                        }
                        if len(self.request_results) >= self.TOTAL_REQUESTS:
                            self.recv_event.set()
                        continue

                    # client端原始记录，不删
                    self.client_copy[msg_id] = {
                        "reply_msg_id": reply.msg_id,
                        "reply_time": reply.time,
                    }

                    # 就近检查1：reply msg_id 必须等于 request msg_id
                    if reply.msg_id != msg_id:
                        self.reply_mismatches += 1
                        self.request_results[msg_id] = {
                            "status": "mismatch_msg_id",
                            "request_msg_id": msg_id,
                            "reply_msg_id": reply.msg_id,
                            "expected_server_ts": self.server_copy.get(msg_id),
                            "reply_time": reply.time,
                            "client_send_ts": send_ts,
                            "client_recv_ts": recv_ts,
                            "latency_ms": latency_ms,
                            "thread": thread_name,
                        }
                        if len(self.request_results) >= self.TOTAL_REQUESTS:
                            self.recv_event.set()
                        continue

                    expected_server_ts = self.server_copy.get(msg_id)

                    # 就近检查2：server_copy 必须存在
                    if expected_server_ts is None:
                        self.request_results[msg_id] = {
                            "status": "missing_server_copy",
                            "request_msg_id": msg_id,
                            "reply_msg_id": reply.msg_id,
                            "expected_server_ts": None,
                            "reply_time": reply.time,
                            "client_send_ts": send_ts,
                            "client_recv_ts": recv_ts,
                            "latency_ms": latency_ms,
                            "thread": thread_name,
                        }
                        if len(self.request_results) >= self.TOTAL_REQUESTS:
                            self.recv_event.set()
                        continue

                    # 就近检查3：payload 必须与 server_copy 完全一致
                    if reply.time != expected_server_ts:
                        self.reply_mismatches += 1
                        self.request_results[msg_id] = {
                            "status": "mismatch_payload",
                            "request_msg_id": msg_id,
                            "reply_msg_id": reply.msg_id,
                            "expected_server_ts": expected_server_ts,
                            "reply_time": reply.time,
                            "client_send_ts": send_ts,
                            "client_recv_ts": recv_ts,
                            "latency_ms": latency_ms,
                            "thread": thread_name,
                        }
                        if len(self.request_results) >= self.TOTAL_REQUESTS:
                            self.recv_event.set()
                        continue

                    # 成功
                    if msg_id in self.client_received:
                        self.client_duplicates += 1

                    self.client_received[msg_id] = {
                        "client_recv_ts": recv_ts,
                        "server_ts": reply.time,
                        "server_copy_ts": expected_server_ts,
                        "reply_msg_id": reply.msg_id,
                        "latency_ms": latency_ms,
                    }

                    self.request_results[msg_id] = {
                        "status": "ok",
                        "request_msg_id": msg_id,
                        "reply_msg_id": reply.msg_id,
                        "expected_server_ts": expected_server_ts,
                        "reply_time": reply.time,
                        "client_send_ts": send_ts,
                        "client_recv_ts": recv_ts,
                        "latency_ms": latency_ms,
                        "thread": thread_name,
                    }

                    if latency_ms is not None:
                        latencies_ms.append(latency_ms)

                    if len(self.request_results) >= self.TOTAL_REQUESTS:
                        self.recv_event.set()

        base = self.TOTAL_REQUESTS // self.CONCURRENCY
        extra = self.TOTAL_REQUESTS % self.CONCURRENCY

        threads = []
        start = time.perf_counter()

        for i in range(self.CONCURRENCY):
            nreq = base + (1 if i < extra else 0)
            if nreq <= 0:
                continue
            t = threading.Thread(
                target=worker,
                args=(nreq,),
                daemon=True,
                name=f"sender-{i}",
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.recv_event.wait(timeout=self.WAIT_TIMEOUT_S)
        end = time.perf_counter()
        elapsed = end - start

        ok_count = 0
        timeout_count = 0
        mismatch_msg_id_count = 0
        mismatch_payload_count = 0
        send_error_count = 0
        missing_server_copy_count = 0
        final_compare_failures = 0
        timestamp_window_failures = 0
        bad_examples = []

        for msg_id in sorted(self.request_results.keys()):
            result = self.request_results[msg_id]
            status = result["status"]

            if status == "ok":
                ok_count += 1

                slack = 0.05
                if not (
                    (result["client_send_ts"] - slack)
                    <= result["expected_server_ts"]
                    <= (result["client_recv_ts"] + slack)
                ):
                    timestamp_window_failures += 1
                    if len(bad_examples) < 10:
                        bad_examples.append((msg_id, status, result))

                # 最终对照：server_copy 和 client_copy 必须一致
                server_ts = self.server_copy.get(msg_id)
                client_entry = self.client_copy.get(msg_id)

                if (
                    server_ts is None
                    or client_entry is None
                    or client_entry["reply_msg_id"] != msg_id
                    or client_entry["reply_time"] != server_ts
                ):
                    final_compare_failures += 1
                    if len(bad_examples) < 10:
                        bad_examples.append(
                            (
                                msg_id,
                                "final_compare_failure",
                                {
                                    "server_ts": server_ts,
                                    "client_entry": client_entry,
                                    "request_result": result,
                                },
                            )
                        )

            elif status == "timeout":
                timeout_count += 1
                if len(bad_examples) < 10:
                    bad_examples.append((msg_id, status, result))

            elif status == "mismatch_msg_id":
                mismatch_msg_id_count += 1
                if len(bad_examples) < 10:
                    bad_examples.append((msg_id, status, result))

            elif status == "mismatch_payload":
                mismatch_payload_count += 1
                if len(bad_examples) < 10:
                    bad_examples.append((msg_id, status, result))

            elif status == "send_error":
                send_error_count += 1
                if len(bad_examples) < 10:
                    bad_examples.append((msg_id, status, result))

            elif status == "missing_server_copy":
                missing_server_copy_count += 1
                if len(bad_examples) < 10:
                    bad_examples.append((msg_id, status, result))

        one_to_one_success_rate = (
            ok_count / self.TOTAL_REQUESTS * 100.0 if self.TOTAL_REQUESTS else 0.0
        )
        throughput = (ok_count / elapsed) if elapsed > 0 else 0.0

        print("\n========== test3_concurrent_requests_one_to_one ==========")
        print(f"total requests            : {self.TOTAL_REQUESTS}")
        print(f"concurrency               : {self.CONCURRENCY}")
        print(f"ok(one-to-one)            : {ok_count}")
        print(f"timeouts                  : {timeout_count}")
        print(f"reply msg_id mismatches   : {mismatch_msg_id_count}")
        print(f"reply payload mismatches  : {mismatch_payload_count}")
        print(f"missing server_copy       : {missing_server_copy_count}")
        print(f"final compare failures    : {final_compare_failures}")
        print(f"send errors               : {send_error_count}")
        print(f"server duplicates         : {self.server_duplicates}")
        print(f"client duplicates         : {self.client_duplicates}")
        print(f"timestamp window failures : {timestamp_window_failures}")
        print(f"one-to-one success rate   : {one_to_one_success_rate:.2f}%")
        print(f"elapsed                   : {elapsed:.6f} s")
        print(f"throughput                : {throughput:.2f} req/s")

        if latencies_ms:
            print(f"latency min               : {min(latencies_ms):.3f} ms")
            print(f"latency avg               : {sum(latencies_ms)/len(latencies_ms):.3f} ms")
            print(f"latency median            : {statistics.median(latencies_ms):.3f} ms")
            print(f"latency p95               : {percentile(latencies_ms, 95):.3f} ms")
            print(f"latency p99               : {percentile(latencies_ms, 99):.3f} ms")
            print(f"latency max               : {max(latencies_ms):.3f} ms")
        else:
            print("latency                   : no successful replies")

        if bad_examples:
            print("bad examples (up to 10):")
            for msg_id, status, result in bad_examples:
                print(f"  msg_id={msg_id}, status={status}, result={result}")

        print("==========================================================\n")

        self.assertEqual(self.send_errors, 0, "there were send errors")
        self.assertEqual(timeout_count, 0, "some requests timed out")
        self.assertEqual(mismatch_msg_id_count, 0, "some requests got replies with wrong msg_id")
        self.assertEqual(mismatch_payload_count, 0, "some replies carried wrong payload compared with server_copy")
        self.assertEqual(missing_server_copy_count, 0, "some msg_ids were missing in server_copy")
        self.assertEqual(final_compare_failures, 0, "final client/server copy comparison failed for some msg_ids")
        self.assertEqual(self.server_duplicates, 0, "server saw duplicate msg_id")
        self.assertEqual(self.client_duplicates, 0, "client received duplicate msg_id")
        self.assertEqual(timestamp_window_failures, 0, "some server timestamps were outside expected send/recv window")
        self.assertEqual(
            ok_count,
            self.TOTAL_REQUESTS,
            f"not all requests passed one-to-one validation: {ok_count}/{self.TOTAL_REQUESTS}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)