# vector_clock_util.py
import threading
import time
import socket
import json
from typing import Callable, Optional, Literal, Union, Dict, Any, Tuple
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeerClient

# ====== 原有：全局整型时钟（保持不动） ======
class VectorClock:
    """全局整型时钟（线程安全单例）"""
    _value = 0
    _lock = threading.RLock()

    @classmethod
    def tick(cls) -> int:
        """自增并返回当前值"""
        with cls._lock:
            cls._value += 1
            return cls._value

    @classmethod
    def peek(cls) -> int:
        """读取当前值（不自增）"""
        with cls._lock:
            return cls._value



class ServerVectorTime:
    def __init__(self, core):
        self.core = core

    
    def tick(self):
        msg=TimestampRequest(self.core.netman.id, self.core.provenance.serverID, self.core.netman.get_mid())
        reply,latency = self.core.netman.send_and_wait_for_reply(self.core.provenance.serverID, msg)
        if not reply:
            raise RuntimeError("ServerVectorTime: no reply received")
        if not isinstance(reply, TimestampReply):
            raise RuntimeError(f"ServerVectorTime: unexpected reply type {type(reply)}")
        return reply.time