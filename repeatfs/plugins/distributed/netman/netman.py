from repeatfs.plugins.distributed.netman.TCP.basicTCP import BasicTCPClient
from repeatfs.plugins.distributed.netman.msg import *
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.utils.logger.basicLogger import Logger
from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.netman.msg import MessagesRegister, msgQueue, MSGencoder
from repeatfs.plugins.distributed.netman.network_message import NodeInfo
from repeatfs.plugins.distributed.utils.units.units import units
from repeatfs.plugins.distributed.netman.waiter import WaiterTable

import threading
import socket


class peerInfo:
    def __init__(self, id, addr, port, conn, closed=False):
        self.addr = addr
        self.conn = conn
        self.port = port
        self.id = id
        self.closed = False
        self.lock = threading.Lock()

    def close(self):
        with self.lock:
            if not self.closed:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.closed = True

    def is_closed(self):
        with self.lock:
            return self.closed


class BasicTCPPeer:
    id = None
    msgQ = None
    cfg = None
    client = None
    peer_conns = None
    local_host = None
    local_port = None
    listen_threads = None
    message_register = None
    mid_lock=None
    next_mid=None
    def __init__(
        self,
        id: ID,
        local_host: str,
        local_port: int,
        msgQ: msgQueue,
        message_register: MessagesRegister,
        cfg: Config,
        replyQueue: msgQueue = None,
        next_mid: int = 0
    ):
        self.replyQueue = replyQueue
        self.id = id
        self.next_mid=next_mid
        self.mid_lock=threading.Lock()
        self.msgQueue = msgQ
        self.cfg = cfg
        self.lock = threading.Lock()
        self.client = BasicTCPClient(local_host, local_port)
        if cfg.netman["nagle"] is True:
            self.client.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
        else:
            self.client.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        self.peer_conns = {}
        self.local_host = local_host
        self.local_port = local_port
        self.listen_threads = {}
        self.message_register = message_register
        self.waiters = WaiterTable()
        self.reply_message_types = set()

        self.message_register.set_netman(self)
        self.message_register.register_message(NodeInfo, self.handleNodeInfo)

        self.msghandling = threading.Thread(
            target=self.message_register.start_handling_messages,
            args=(self.msgQueue,),
            name="msghandling",
            daemon=True,
        )
        self.msghandling.start()

    def get_mid(self):
        with self.mid_lock:
            mid = self.next_mid
            self.next_mid += 1
            return mid
        
    def register_message(self, msg_type, handler):
        self.message_register.register_message(msg_type, handler)

    def register_message_type(self, msg_type):
        self.message_register.register_message_type(msg_type)   

    def register_reply_message(self, msg_type):
        self.reply_message_types.add(msg_type)

    def _recv_exact(self, conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _send_encoded(self, to, encoded_msg):
        if str(to) not in self.peer_conns:
            keys_list = list(self.peer_conns.keys())
            keys_str = ", ".join(str(key) for key in keys_list)
            Logger.error(
                f"Cannot send to {to}. Not connected. Available connections: {keys_str}"
            )
            return False

        try:
            self.peer_conns[str(to)].conn.sendall(encoded_msg)
            return True
        except Exception as e:
            Logger.error(f"Error sending message to {to}: {e}")
            return False

    def send_in_background(self, to, msg):
        thread = threading.Thread(target=self.send, args=(to, msg), daemon=True)
        thread.start()

    def send(self, to, msg):
        encoded_msg = MSGencoder.encode(msg)
        return self._send_encoded(to, encoded_msg)

    def send_and_wait_for_reply(self, to, msg, timeout=1):
        """
        Send a request and wait synchronously for a reply whose msg_id equals request.msg_id.

        Caller must provide msg.msg_id before calling.

        Returns:
            (reply_msg, latency_ms) on success
            (None, None) on timeout/failure
        """
        msg_id = getattr(msg, "msg_id", None)
        if msg_id is None:
            Logger.error("send_and_wait_for_reply requires msg.msg_id")
            return None, None

        _, waiter = self.waiters.create(msg_id=msg_id)

        try:
            encoded_msg = MSGencoder.encode(msg)
            ok = self._send_encoded(to, encoded_msg)
            if not ok:
                return None, None

            reply = waiter.wait(timeout=timeout)
            if reply is None:
                return None, None

            return reply, waiter.latency_ms
        finally:
            self.waiters.remove(msg_id)

    def reply(self, req_msg, reply_msg):
        """
        Reply to a request message. reply_msg.msg_id will match req_msg.msg_id.
        """
        setattr(reply_msg, "msg_id", getattr(req_msg, "msg_id", None))
        return req_msg.reply(reply_msg)

    def is_connected(self, to):
        return str(to) in self.peer_conns

    def close(self):
        with self.lock:
            self.message_register.stop_handling_messages()
            for peer_id, pinfo in self.peer_conns.items():
                pinfo.close()
            for thread in self.listen_threads.values():
                thread.join(timeout=1)
        self.client.close()

    def listen(self):
        self.client.socket.listen()
        Logger.info(f"Listening on {self.client.local_host}:{self.client.listen_port}")
        while True:
            if self.client.is_closed():
                Logger.debug(f"{self.id}: Client is closed. Exiting listen thread.")
                return
            try:
                self.client.socket.settimeout(1)
                try:
                    conn, addr = self.client.socket.accept()
                    if self.cfg.netman["nagle"] is True:
                        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
                        print(f"Enabled Nagle's algorithm for connection from {addr}")
                    else:
                        print(f"Disabled Nagle's algorithm for connection from {addr}")
                        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except socket.timeout:
                    continue
                except Exception:
                    continue

                if conn is None:
                    Logger.warning(f"{self.id}: Failed to accept connection")
                    continue

                Logger.info(f"{self.id}: Accepted connection from {addr}")

                raw_size = self._recv_exact(conn, 4)
                if raw_size is None:
                    Logger.warning(f"{self.id}: Failed to receive message size from {addr}")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue

                size = MSGencoder.decode_length(raw_size)
                if size:
                    Logger.debug(f"{self.id}: Received size {size}")
                    encoded_msg = self._recv_exact(conn, size)
                    if encoded_msg is None:
                        Logger.warning(f"{self.id}: Failed to receive full message from {addr}")
                        try:
                            conn.close()
                        except Exception:
                            pass
                        continue

                    decoded_msg = MSGencoder.decode(encoded_msg)
                    Logger.debug(f"{self.id}: Received {decoded_msg}")
                    decoded_msg.record_conn(conn)
                    decoded_msg.record_queue(self.msgQueue)
                    self.msgQueue.push(decoded_msg)

            except Exception as e:
                Logger.error(f"Error accepting connection: {e}")
                continue

    def connect_to_peer(self, host, port):
        conn = self.client.connect_to_peer(host, port)
        if conn is None:
            return False

        Logger.info(f"{self.id}: Connected to {host}:{port}")

        myinfo = NodeInfo(
            self.id, self.local_host, self.local_port, is_client=False, is_reply=False
        )
        encoded_msg = MSGencoder.encode(myinfo)
        conn.sendall(encoded_msg)
        Logger.info(f"{self.id}: Sent my info to {host}:{port}")

        raw_size = self._recv_exact(conn, 4)
        if raw_size is None:
            Logger.error(f"{self.id}: Failed to receive reply size from {host}:{port}")
            try:
                conn.close()
            except Exception:
                pass
            return False

        size = MSGencoder.decode_length(raw_size)
        Logger.debug(f"{self.id}: Received reply of size {size}")

        encoded_msg = self._recv_exact(conn, size)
        if encoded_msg is None:
            Logger.error(f"{self.id}: Failed to receive full reply from {host}:{port}")
            try:
                conn.close()
            except Exception:
                pass
            return False

        decoded_msg = MSGencoder.decode(encoded_msg)
        decoded_msg.record_conn(conn)
        decoded_msg.record_queue(self.msgQueue)
        self.msgQueue.push(decoded_msg)
        return True

    def listen_on_conn(self, peerinfo):
        peerinfo.conn.settimeout(units.ms_to_s(self.cfg.netman["conn_recv_timeout_ms"]))
        while True:
            if peerinfo.is_closed():
                break
            try:
                try:
                    raw_size = peerinfo.conn.recv(4)
                except socket.timeout:
                    continue

                if len(raw_size) == 0:
                    break
                if len(raw_size) < 4:
                    continue

                size = MSGencoder.decode_length(raw_size)
                encoded_msg = b""
                while len(encoded_msg) < size:
                    try:
                        part = peerinfo.conn.recv(size - len(encoded_msg))
                    except socket.timeout:
                        break
                    if not part:
                        break
                    encoded_msg += part

                if len(encoded_msg) < size:
                    continue

                decoded_msg = MSGencoder.decode(encoded_msg)
                decoded_msg.record_conn(peerinfo.conn)
                decoded_msg.record_queue(self.msgQueue)

                if type(decoded_msg) in self.reply_message_types:
                    msg_id = getattr(decoded_msg, "msg_id", None)
                    if msg_id is not None and self.waiters.complete(msg_id, decoded_msg):
                        continue

                self.msgQueue.push(decoded_msg)

            except Exception as e:
                Logger.error(f"Unexpected error: {e}")
                break

        peerinfo.close()
        Logger.info(f"{self.id}: Connection to {peerinfo.id} closed")

    def handleNodeInfo(self, netman, msg):
        Logger.info(f"{netman.id}: Received NodeInfo from {msg.id}")
        if msg.is_client:
            Logger.warning(f"{netman.id}: Received NodeInfo from client. Ignoring.")
            return

        pinfo = peerInfo(msg.id, msg.addr, msg.port, msg.conn)
        netman.peer_conns[str(msg.id)] = pinfo
        listen_thread = threading.Thread(
            target=netman.listen_on_conn,
            args=(pinfo,),
            daemon=True,
            name=f"listen_{netman.id}<->{msg.id}",
        )
        listen_thread.start()
        netman.listen_threads[msg.id] = listen_thread

        if msg.is_reply:
            Logger.debug(
                f"{netman.id}: Received reply from {msg.id}. Not sending my info."
            )
            return

        Logger.debug(f"{netman.id}: Replying with my info to {msg.id}")
        myinfo = NodeInfo(
            netman.id,
            netman.local_host,
            netman.local_port,
            is_client=False,
            is_reply=True,
        )
        encoded_msg = MSGencoder.encode(myinfo)
        msg.conn.sendall(encoded_msg)


class BasicTCPPeerClient:
    id = None
    msgQ = None
    cfg = None
    client = None
    peer_conns = None
    listen_threads = None
    message_register = None
    mid_lock=None
    next_mid=None
    def __init__(
        self,
        id,
        local_host,
        local_port,
        msgQ,
        message_register,
        cfg,
        replyQueue=None,
        next_mid=0,
    ):
        self.replyQueue = replyQueue
        self.id = id
        self.next_mid=next_mid
        self.msgQueue = msgQ
        self.cfg = cfg
        self.mid_lock=threading.Lock()
        self.local_host = local_host
        self.local_port = local_port
        self.lock = threading.Lock()
        self.client = BasicTCPClient(local_host, local_port, is_client=True)

        if cfg.netman["nagle"] is True:
            self.client.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
        else:
            self.client.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        self.peer_conns = {}
        self.listen_threads = {}
        self.message_register = message_register
        self.waiters = WaiterTable()
        self.reply_message_types = set()

        self.message_register.set_netman(self)
        self.message_register.register_message(NodeInfo, self.handleNodeInfo)

        self.msghandling = threading.Thread(
            target=self.message_register.start_handling_messages,
            args=(self.msgQueue,),
            name="msghandling",
            daemon=True,
        )
        self.msghandling.start()

    def get_mid(self):
        with self.mid_lock:
            mid = self.next_mid
            self.next_mid += 1
            return mid
    
    def register_message(self, msg_type, handler):
        self.message_register.register_message(msg_type, handler)

    def register_reply_message(self, msg_type):
        self.reply_message_types.add(msg_type)
    
    def register_message_type(self, msg_type):
        self.message_register.register_message_type(msg_type)   

    def _recv_exact(self, conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _send_encoded(self, to, encoded_msg):
        if str(to) not in self.peer_conns:
            keys_list = list(self.peer_conns.keys())
            keys_str = ", ".join(str(key) for key in keys_list)
            Logger.error(
                f"Cannot send to {to}. Not connected. Available connections: {keys_str}"
            )
            return False

        try:
            self.peer_conns[str(to)].conn.sendall(encoded_msg)
            return True
        except Exception as e:
            Logger.error(f"Error sending message to {to}: {e}")
            return False

    def send_in_background(self, to, msg):
        thread = threading.Thread(target=self.send, args=(to, msg), daemon=True)
        thread.start()

    def send(self, to, msg):
        encoded_msg = MSGencoder.encode(msg)
        return self._send_encoded(to, encoded_msg)

    def send_and_wait_for_reply(self, to, msg, timeout=1):
        """
        Send a request and wait synchronously for a reply whose msg_id equals request.msg_id.

        Caller must provide msg.msg_id before calling.

        Returns:
            (reply_msg, latency_ms) on success
            (None, None) on timeout/failure
        """
        msg_id = getattr(msg, "msg_id", None)
        if msg_id is None:
            Logger.error("send_and_wait_for_reply requires msg.msg_id, msg: {}".format(msg))
            return None, None

        _, waiter = self.waiters.create(msg_id=msg_id)

        try:
            encoded_msg = MSGencoder.encode(msg)
            ok = self._send_encoded(to, encoded_msg)
            if not ok:
                Logger.error(f"Failed to send message{msg} to {to}")
                return None, None

            reply = waiter.wait(timeout=timeout)
            if reply is None:
                return None, None

            return reply, waiter.latency_ms
        finally:
            self.waiters.remove(msg_id)

    def reply(self, req_msg, reply_msg):
        setattr(reply_msg, "msg_id", getattr(req_msg, "msg_id", None))
        return req_msg.reply(reply_msg)

    def is_connected(self, to):
        return str(to) in self.peer_conns

    def close(self):
        with self.lock:
            self.message_register.stop_handling_messages()
            for peer_id, pinfo in self.peer_conns.items():
                pinfo.close()
            for thread in self.listen_threads.values():
                thread.join(timeout=1)
        self.client.close()

    def connect_to_peer(self, host, port):
        conn = self.client.connect_to_peer(host, port)
        if conn is None:
            return False
        if self.cfg.netman["nagle"] is True:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
        else:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        
        Logger.info(f"{self.id}: Connected to {host}:{port}")

        myinfo = NodeInfo(
            self.id, self.local_host, self.local_port, is_client=False, is_reply=False
        )
        encoded_msg = MSGencoder.encode(myinfo)
        conn.sendall(encoded_msg)
        Logger.info(f"{self.id}: Sent my info to {host}:{port}")

        raw_size = self._recv_exact(conn, 4)
        if raw_size is None:
            Logger.error(f"{self.id}: Failed to receive reply size from {host}:{port}")
            try:
                conn.close()
            except Exception:
                pass
            return False

        size = MSGencoder.decode_length(raw_size)
        Logger.debug(f"{self.id}: Received reply of size {size}")

        encoded_msg = self._recv_exact(conn, size)
        if encoded_msg is None:
            Logger.error(f"{self.id}: Failed to receive full reply from {host}:{port}")
            try:
                conn.close()
            except Exception:
                pass
            return False

        decoded_msg = MSGencoder.decode(encoded_msg)
        decoded_msg.record_conn(conn)
        decoded_msg.record_queue(self.msgQueue)
        self.msgQueue.push(decoded_msg)
        return True

    def listen_on_conn(self, peerinfo):
        peerinfo.conn.settimeout(units.ms_to_s(self.cfg.netman["conn_recv_timeout_ms"]))
        while True:
            if peerinfo.is_closed():
                break
            try:
                try:
                    raw_size = peerinfo.conn.recv(4)
                except socket.timeout:
                    continue

                if len(raw_size) == 0:
                    break
                if len(raw_size) < 4:
                    continue

                size = MSGencoder.decode_length(raw_size)
                encoded_msg = b""
                while len(encoded_msg) < size:
                    try:
                        part = peerinfo.conn.recv(size - len(encoded_msg))
                    except socket.timeout:
                        break
                    if not part:
                        break
                    encoded_msg += part

                if len(encoded_msg) < size:
                    continue

                decoded_msg = MSGencoder.decode(encoded_msg)
                decoded_msg.record_conn(peerinfo.conn)
                decoded_msg.record_queue(self.msgQueue)

                if type(decoded_msg) in self.reply_message_types:
                    msg_id = getattr(decoded_msg, "msg_id", None)
                    if msg_id is not None and self.waiters.complete(msg_id, decoded_msg):
                        continue

                self.msgQueue.push(decoded_msg)

            except Exception as e:
                Logger.error(f"Unexpected error: {e}")
                break

        peerinfo.close()
        Logger.info(f"{self.id}: Connection to {peerinfo.id} closed")

    def handleNodeInfo(self, netman, msg):
        Logger.info(f"{netman.id}: Received NodeInfo from {msg.id}")
        if msg.is_client:
            Logger.warning(f"{netman.id}: Received NodeInfo from client. Ignoring.")
            return

        pinfo = peerInfo(msg.id, msg.addr, msg.port, msg.conn)
        netman.peer_conns[str(msg.id)] = pinfo
        listen_thread = threading.Thread(
            target=netman.listen_on_conn,
            args=(pinfo,),
            daemon=True,
            name=f"listen_{netman.id}<->{msg.id}",
        )
        listen_thread.start()
        netman.listen_threads[msg.id] = listen_thread

        if msg.is_reply:
            Logger.debug(
                f"{netman.id}: Received reply from {msg.id}. Not sending my info."
            )
            return

        Logger.debug(f"{netman.id}: Replying with my info to {msg.id}")
        myinfo = NodeInfo(
            netman.id,
            netman.local_host,
            netman.local_port,
            is_client=False,
            is_reply=True,
        )
        encoded_msg = MSGencoder.encode(myinfo)
        msg.conn.sendall(encoded_msg)