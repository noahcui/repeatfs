import socket
import threading
from logger.basicLogger import Logger


class BasicTCPClient:
    def __init__(self, local_host, listen_port, is_client=False):
        self.lock = threading.Lock()
        self.closed = False
        if not is_client:
            self.local_host = local_host
            self.listen_port = listen_port
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.socket.bind((self.local_host, self.listen_port))
            except Exception as e:
                Logger.error(f"Failed to bind to {self.local_host}:{self.listen_port}")
                Logger.error(e)
                self.close()
        else:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def connect_to_peer(self, peer_host, peer_port):
        try:
            peer_conn = socket.create_connection((peer_host, peer_port))
            return peer_conn
        except Exception as e:
            Logger.error(f"Failed to connect to {peer_host}:{peer_port}")
            Logger.error(e)
            return None

    def close(self):
        with self.lock:
            self.closed = True
        self.socket.close()
        Logger.info("Closed connection")

    def is_closed(self):
        with self.lock:
            return self.closed
