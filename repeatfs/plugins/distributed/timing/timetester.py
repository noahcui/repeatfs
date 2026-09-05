# tests/test_server_vector_time.py
import threading
import time
import socket
import unittest
import statistics
import random

from timing import (
    VectorTimeServer,
    ServerVectorTime,
    GetTime,
)


def _wait_port_open(host: str, port: int, timeout: float = 2.0) -> None:
    """简单等 server 把端口 bind 起来，避免 race."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Server {host}:{port} did not start in time")


class TestServerVectorTime(unittest.TestCase):
    HOST = "127.0.0.1"
    PORT = 5000

    @classmethod
    def setUpClass(cls) -> None:
        # 启动 TCP server（后台线程）
        cls._server = VectorTimeServer(host=cls.HOST, port=cls.PORT)
        t = threading.Thread(target=cls._server.serve_forever, daemon=True)
        t.start()

        _wait_port_open(cls.HOST, cls.PORT)

        # 配置 client 端
        ServerVectorTime.configure(cls.HOST, cls.PORT, timeout=1.0)

    # ====== 基础功能测试 ======
    def test_basic_tick_and_peek(self) -> None:
        """TICK 应该自增，PEEK 不自增。"""
        node = "basic-node"

        vec1 = ServerVectorTime.tick(node)
        self.assertIn(node, vec1)
        # 不要求固定值（可能被其他测试污染），只要求是 int 且 >=1
        self.assertIsInstance(vec1[node], int)
        self.assertGreaterEqual(vec1[node], 1)

        vec2 = ServerVectorTime.tick(node)
        self.assertEqual(vec2[node], vec1[node] + 1)

        vec3 = ServerVectorTime.peek(node)
        self.assertEqual(vec3[node], vec2[node])  # peek 不自增

    def test_gettime_server_vector(self) -> None:
        """GetTime(mode='server_vector') 应该只返回该 node 的标量。"""
        GetTime.configure("server_vector")
        node = "gettime-node"

        name1, v1 = GetTime.get_time(node_id=node, auto_tick=True)
        self.assertEqual(name1, node)
        self.assertIsInstance(v1, int)

        # peek 不自增
        name2, v2 = GetTime.get_time(node_id=node, auto_tick=False)
        self.assertEqual(name2, node)
        self.assertEqual(v2, v1)

    # ====== 复杂并发测试 ======
    def test_heavy_concurrent_mixed_nodes(self) -> None:
        """
        复杂并发测试：
          - 多线程
          - 多个 node_id（有共享也有独立）
          - 检查增量是否跟预期一致
        """
        base = "heavy-node"
        num_threads = 20
        ticks_per_thread = 1000

        # 让多个线程复用少量 node_id，制造竞争
        node_ids = [f"{base}-{i % 4}" for i in range(num_threads)]  # 4 个 node

        # 记录每个 node 在本测试中的“期望增量”
        expected_delta = {nid: 0 for nid in set(node_ids)}
        delta_lock = threading.Lock()

        # 记录 baseline（测试前的值），以避免受其他测试影响
        baseline_vec = ServerVectorTime.peek("baseline")
        baseline = {nid: baseline_vec.get(nid, 0) for nid in expected_delta.keys()}

        def worker(idx: int) -> None:
            nid = node_ids[idx]
            local_count = 0
            for _ in range(ticks_per_thread):
                ServerVectorTime.tick(nid)
                local_count += 1

                # 随机 sleep 一点点，打乱访问顺序（模拟复杂并发）
                if random.random() < 0.1:
                    time.sleep(random.random() * 0.002)

            # 把该线程对该 node 的贡献合并到 expected_delta
            with delta_lock:
                expected_delta[nid] += local_count

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 并发结束后查看每个 node 的最终值
        final_vec = ServerVectorTime.peek("after-heavy")
        for nid, delta in expected_delta.items():
            before = baseline.get(nid, 0)
            after = final_vec.get(nid, 0)
            self.assertEqual(
                after,
                before + delta,
                msg=f"Node {nid}: before={before}, delta={delta}, after={after}",
            )

    # ====== 延迟测试（测平均 latency） ======
    def _measure_latency(
        self,
        node: str = "latency-node",
        rounds: int = 500,
    ) -> float:
        """
        连续发 rounds 次 TICK，测每次往返的 latency（秒），返回平均值。
        """
        durations = []

        # 预热几次，避免冷启动影响
        for _ in range(10):
            ServerVectorTime.tick(node)

        for _ in range(rounds):
            t0 = time.perf_counter()
            ServerVectorTime.tick(node)
            t1 = time.perf_counter()
            durations.append(t1 - t0)

        avg = statistics.mean(durations)
        # 也可以顺便看看 95 百分位
        p95 = statistics.quantiles(durations, n=100)[94]

        print(
            f"[LatencyTest] node={node}, rounds={rounds}, "
            f"avg={avg * 1000:.3f} ms, p95={p95 * 1000:.3f} ms"
        )

        # 保证有意义（>0）
        self.assertGreater(avg, 0.0)
        return avg

    def test_latency_roundtrip(self) -> None:
        """
        延迟测试：测 ServerVectorTime.tick 的平均往返时间。
        这里只做测量和基本 sanity check，不对具体数值做强约束。
        """
        avg = self._measure_latency(node="latency-node", rounds=500)

        # 这里不强行 assert 一个硬阈值，环境差异太大；
        # 如果你想在本机上卡一个上限，比如 < 5ms，可以打开下面一行：
        # self.assertLess(avg, 0.005, f"平均延迟过高: {avg * 1000:.3f} ms")
        self.assertGreater(avg, 0.0)
    
    def test_heavy_concurrent_no_goback(self) -> None:
        """
        并发压力测试：
          - 多线程 + 多个 node_id（有共享有独立）
          - 确认：
              1) 每个 node 的计数不会回退（所有取值严格单调递增）
              2) 最终值 = baseline + 所有 tick 调用次数之和
        """
        base = "heavy-node"
        num_threads = 20
        ticks_per_thread = 1000

        # 多个线程复用 4 个 node_id，制造竞争
        node_ids = [f"{base}-{i % 4}" for i in range(num_threads)]  # 4 个 node

        # 每个 node 的总调用次数（期望增量）
        expected_delta: Dict[str, int] = {nid: 0 for nid in set(node_ids)}
        delta_lock = threading.Lock()

        # 记录每个 node 所有 tick 返回值，用来做“不回退”检查
        recorded_values: Dict[str, List[int]] = {nid: [] for nid in expected_delta}
        values_lock = threading.Lock()

        # baseline（测试前 snapshot），避免受其他测试干扰
        baseline_vec = ServerVectorTime.peek("baseline")
        baseline: Dict[str, int] = {
            nid: int(baseline_vec.get(nid, 0)) for nid in expected_delta.keys()
        }

        def extract_value(ret: any, nid: str) -> int:
            """
            从 tick() 的返回值里抽出该 node 的计数。
            - 如果直接是 int，就直接用
            - 如果是 dict，尝试几种常见结构：
                {"clock": {nid: val}} 或 {nid: val}
            你可以按自己实际的接口改造这段逻辑。
            """
            if isinstance(ret, int):
                return ret
            if isinstance(ret, dict):
                if "clock" in ret and isinstance(ret["clock"], dict):
                    return int(ret["clock"][nid])
                if nid in ret:
                    return int(ret[nid])
            raise AssertionError(f"Cannot extract value for node {nid} from tick() return: {ret!r}")

        def worker(idx: int) -> None:
            nid = node_ids[idx]
            local_count = 0
            local_vals: List[int] = []

            # 同一线程内“不回退”检查：后一次要大于前一次
            last_val: int | None = None

            for _ in range(ticks_per_thread):
                ret = ServerVectorTime.tick(nid)
                cur_val = extract_value(ret, nid)

                # 同一线程内：后发必然 > 前一次（不回退、不持平）
                if last_val is not None:
                    if cur_val <= last_val:
                        raise AssertionError(
                            f"[thread-{idx}] node {nid}: value went backwards or stayed: "
                            f"prev={last_val}, cur={cur_val}"
                        )
                last_val = cur_val

                local_vals.append(cur_val)
                local_count += 1

                # 打乱访问顺序，模拟更复杂并发
                if random.random() < 0.1:
                    time.sleep(random.random() * 0.002)

            # 合并到全局统计
            with delta_lock:
                expected_delta[nid] += local_count
            with values_lock:
                recorded_values[nid].extend(local_vals)

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 并发结束后查看最终向量
        final_vec = ServerVectorTime.peek("after-heavy")

        for nid in expected_delta.keys():
            before = baseline.get(nid, 0)
            after = int(final_vec.get(nid, 0))
            delta = expected_delta[nid]

            # 1) 原来的增量校验：最终值 == baseline + 所有 tick 次数
            self.assertEqual(
                after,
                before + delta,
                msg=f"Node {nid}: before={before}, delta={delta}, after={after}",
            )

            # 2) 从全局视角再检查一次“不回退”（对值排序后严格单调递增）
            vals = recorded_values[nid]
            # 每次 tick 都应该有一个返回值
            self.assertEqual(
                len(vals),
                delta,
                msg=f"Node {nid}: recorded {len(vals)} values but expected delta={delta}",
            )

            vals_sorted = sorted(vals)
            for i in range(1, len(vals_sorted)):
                self.assertGreater(
                    vals_sorted[i],
                    vals_sorted[i - 1],
                    msg=(
                        f"Node {nid}: value went backwards or stayed when sorted: "
                        f"{vals_sorted[i-1]} -> {vals_sorted[i]}"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
