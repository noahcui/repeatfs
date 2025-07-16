from netman.TCP.basicTCP import BasicTCPClient
from netman.msg import *
from config.config import Config
from logger.basicLogger import Logger
from ids.nodeID import ID
from netman.msg import MessagesRegister, msgQueue, MSGencoder
from netman.network_message import NodeInfo
from utils.units.units import units
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
            self.conn.close()
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

    def __init__(
        self,
        id: ID,
        local_host: str,
        local_port: int,
        msgQ: msgQueue,
        message_register: MessagesRegister,
        cfg: Config,
        replyQueue: msgQueue = None,
    ):
        self.replyQueue = replyQueue
        self.id = id
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
        self.message_register.set_netman(self)
        self.message_register.register_message(NodeInfo, self.handleNodeInfo)
        self.msghandling = threading.Thread(
            target=self.message_register.start_handling_messages,
            args=(self.msgQueue,),
            name="msghandling",
            daemon=True,
        )
        self.msghandling.start()
        # self.message_register = MessagesRegister()
        # MessagesRegister.register_message(NodeInfo, self.handleNodeInfo)

    def register_message(self, msg_type, handler):
        self.message_register.register_message(msg_type, handler)

    def send_in_background(self, to, msg):
        thread = threading.Thread(target=self.send, args=(to, msg), daemon=True)
        thread.start()

    def send(self, to, msg):
        if str(to) not in self.peer_conns:
            keys_list = list(self.peer_conns.keys())
            keys_str = ", ".join(str(key) for key in keys_list)
            Logger.error(
                f"Cannot send to {to}. Not connected. Available connections: {keys_str}"
            )
            return

        encoded_msg = MSGencoder.encode(msg)
        try:
            self.peer_conns[str(to)].conn.send(encoded_msg)
        except Exception as e:
            Logger.error(f"Error sending message: {e}")

    def is_connected(self, to):
        return str(to) in self.peer_conns

    def close(self):
        with self.lock:
            self.message_register.stop_handling_messages()
            for peer_id, conn in self.peer_conns.items():
                conn.close()
            for thread in self.listen_threads.values():
                thread.join()

            # self.msghandling.join()
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
                except:
                    continue
                if conn is None:
                    Logger.warning(f"{self.id}: Failed to accept connection")
                    continue
                Logger.info(f"{self.id}: Accepted connection from {addr}")
                # wait for peer's info
                size = MSGencoder.decode_length(conn.recv(4))
                if size:
                    Logger.debug(f"{self.id}: Received size {size}")
                    encoded_msg = conn.recv(size)

                    decoded_msg = MSGencoder.decode(encoded_msg)
                    Logger.debug(f"{self.id}: Received {decoded_msg}")
                    decoded_msg.record_conn(conn)
                    decoded_msg.record_queue(self.msgQueue)
                    self.msgQueue.push(decoded_msg)
            except Exception as e:
                Logger.error(f"Error accepting connection: {e}")
                continue

    # estabilsh connection and exchange ids.
    def connect_to_peer(self, host, port):
        conn = self.client.connect_to_peer(host, port)
        if conn is None:
            return False

        Logger.info(f"{self.id}: Connected to {host}:{port}")
        # send my info to peer
        myinfo = NodeInfo(
            self.id, self.local_host, self.local_port, is_client=False, is_reply=False
        )
        encoded_msg = MSGencoder.encode(myinfo)
        conn.send(encoded_msg)
        Logger.info(f"{self.id}: Sent my info to {host}:{port}")

        # wait for peer's info
        size = MSGencoder.decode_length(conn.recv(4))
        Logger.debug(f"{self.id}: Received reply of size {size}")
        encoded_msg = conn.recv(size)
        decoded_msg = MSGencoder.decode(encoded_msg)
        decoded_msg.record_conn(conn)
        decoded_msg.record_queue(self.msgQueue)
        self.msgQueue.push(decoded_msg)
        return True

    def listen_on_conn(self, pearinfo):
        pearinfo.conn.settimeout(units.ms_to_s(self.cfg.netman["conn_recv_timeout_ms"]))
        while True:
            if pearinfo.is_closed():
                # Logger.info(f"{self.id}: Connection to {pearinfo.id} is closed. Exiting listen thread.")
                break
            try:
                try:
                    raw_size = pearinfo.conn.recv(4)
                except socket.timeout:
                    continue
                if len(raw_size) < 4:
                    continue
                size = MSGencoder.decode_length(raw_size)
                encoded_msg = b""
                while len(encoded_msg) < size:
                    try:
                        part = pearinfo.conn.recv(size - len(encoded_msg))
                    except socket.timeout:
                        break
                    if not part:
                        break
                    encoded_msg += part
                if len(encoded_msg) < size:
                    continue
                decoded_msg = MSGencoder.decode(encoded_msg)
                decoded_msg.record_conn(pearinfo.conn)
                decoded_msg.record_queue(self.msgQueue)
                self.msgQueue.push(decoded_msg)
            except Exception as e:
                Logger.error(f"Unexpected error: {e}")
                break
        Logger.info(f"{self.id}: Connection to {pearinfo.id} closed")
        # pearinfo.conn.close()

    def handleNodeInfo(self, netman, msg):
        Logger.info(f"{netman.id}: Received NodeInfo from {msg.id}")
        if msg.is_client:
            Logger.warning("{netman.id}: Received NodeInfo from client. Ignoring.")
            return

        # Add peer to peer_conns and start listening on the connection
        pearinfo = peerInfo(msg.id, msg.addr, msg.port, msg.conn)
        netman.peer_conns[str(msg.id)] = pearinfo
        listen_thread = threading.Thread(
            target=netman.listen_on_conn,
            args=(pearinfo,),
            daemon=True,
            name=f"listen_{netman.id}<->{msg.id}",
        )
        listen_thread.start()
        netman.listen_threads[msg.id] = listen_thread

        # If this is a reply, do not send my info to peer again.
        if msg.is_reply:
            Logger.debug(
                f"{netman.id}: Received reply from {msg.id}. Not sending my info."
            )
            return

        Logger.debug(f"{netman.id}: Replying with my info to {msg.id}")
        # Ohterwise, send my info to peer
        myinfo = NodeInfo(
            netman.id,
            netman.local_host,
            netman.local_port,
            is_client=False,
            is_reply=True,
        )
        encoded_msg = MSGencoder.encode(myinfo)
        msg.conn.send(encoded_msg)


class BasicTCPPeerClient:
    id = None
    msgQ = None
    cfg = None
    client = None
    peer_conns = None
    listen_threads = None
    message_register = None

    def __init__(
        self,
        id,
        local_host,
        local_port,
        msgQ,
        message_register,
        cfg,
        replyQueue=None,
    ):
        self.replyQueue = replyQueue
        self.id = id
        self.msgQueue = msgQ
        self.cfg = cfg
        self.lock = threading.Lock()
        self.client = BasicTCPClient(local_host, local_port, is_client=True)
        if cfg.netman["nagle"] is True:
            self.client.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
        else:
            self.client.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.peer_conns = {}
        self.listen_threads = {}
        self.message_register = message_register
        self.message_register.set_netman(self)
        self.message_register.register_message(NodeInfo, self.handleNodeInfo)
        self.msghandling = threading.Thread(
            target=self.message_register.start_handling_messages,
            args=(self.msgQueue,),
            name="msghandling",
            daemon=True,
        )
        self.msghandling.start()

    def register_message(self, msg_type, handler):
        self.message_register.register_message(msg_type, handler)

    def send_in_background(self, to, msg):
        thread = threading.Thread(target=self.send, args=(to, msg), daemon=True)
        thread.start()

    def send(self, to, msg):
        if str(to) not in self.peer_conns:
            keys_list = list(self.peer_conns.keys())
            keys_str = ", ".join(str(key) for key in keys_list)
            Logger.error(
                f"Cannot send to {to}. Not connected. Available connections: {keys_str}"
            )
            return

        encoded_msg = MSGencoder.encode(msg)
        try:
            self.peer_conns[str(to)].conn.send(encoded_msg)
        except Exception as e:
            Logger.error(f"Error sending message: {e}")

    def is_connected(self, to):
        return str(to) in self.peer_conns

    def close(self):
        with self.lock:
            self.message_register.stop_handling_messages()
            for peer_id, conn in self.peer_conns.items():
                conn.close()
            for thread in self.listen_threads.values():
                thread.join()
        self.client.close()

    # estabilsh connection and exchange ids.
    def connect_to_peer(self, host, port):
        conn = self.client.connect_to_peer(host, port)
        if conn is None:
            return False

        Logger.info(f"{self.id}: Connected to {host}:{port}")
        # send my info to peer
        myinfo = NodeInfo(
            self.id, self.local_host, self.local_port, is_client=False, is_reply=False
        )
        encoded_msg = MSGencoder.encode(myinfo)
        conn.send(encoded_msg)
        Logger.info(f"{self.id}: Sent my info to {host}:{port}")

        # wait for peer's info
        size = MSGencoder.decode_length(conn.recv(4))
        Logger.debug(f"{self.id}: Received reply of size {size}")
        encoded_msg = conn.recv(size)
        decoded_msg = MSGencoder.decode(encoded_msg)
        decoded_msg.record_conn(conn)
        decoded_msg.record_queue(self.msgQueue)
        self.msgQueue.push(decoded_msg)
        return True

    def listen_on_conn(self, pearinfo):
        pearinfo.conn.settimeout(units.ms_to_s(self.cfg.netman["conn_recv_timeout_ms"]))
        while True:
            if pearinfo.is_closed():
                # Logger.info(f"{self.id}: Connection to {pearinfo.id} is closed. Exiting listen thread.")
                break
            try:
                try:
                    raw_size = pearinfo.conn.recv(4)
                except socket.timeout:
                    continue
                if len(raw_size) < 4:
                    continue
                size = MSGencoder.decode_length(raw_size)
                encoded_msg = b""
                while len(encoded_msg) < size:
                    try:
                        part = pearinfo.conn.recv(size - len(encoded_msg))
                    except socket.timeout:
                        break
                    if not part:
                        break
                    encoded_msg += part
                if len(encoded_msg) < size:
                    continue
                decoded_msg = MSGencoder.decode(encoded_msg)
                decoded_msg.record_conn(pearinfo.conn)
                decoded_msg.record_queue(self.msgQueue)
                self.msgQueue.push(decoded_msg)
            except Exception as e:
                Logger.error(f"Unexpected error: {e}")
                break
        Logger.info(f"{self.id}: Connection to {pearinfo.id} closed")
        # pearinfo.conn.close()

    def handleNodeInfo(self, netman, msg):
        Logger.info(f"{netman.id}: Received NodeInfo from {msg.id}")
        if msg.is_client:
            Logger.warning("{netman.id}: Received NodeInfo from client. Ignoring.")
            return

        # Add peer to peer_conns and start listening on the connection
        pearinfo = peerInfo(msg.id, msg.addr, msg.port, msg.conn)
        netman.peer_conns[str(msg.id)] = pearinfo
        listen_thread = threading.Thread(
            target=netman.listen_on_conn,
            args=(pearinfo,),
            daemon=True,
            name=f"listen_{netman.id}<->{msg.id}",
        )
        listen_thread.start()
        netman.listen_threads[msg.id] = listen_thread

        # If this is a reply, do not send my info to peer again.
        if msg.is_reply:
            Logger.debug(
                f"{netman.id}: Received reply from {msg.id}. Not sending my info."
            )
            return

        Logger.debug(f"{netman.id}: Replying with my info to {msg.id}")
        # Ohterwise, send my info to peer
        myinfo = NodeInfo(
            netman.id,
            netman.local_host,
            netman.local_port,
            is_client=False,
            is_reply=True,
        )
        encoded_msg = MSGencoder.encode(myinfo)
        msg.conn.send(encoded_msg)
