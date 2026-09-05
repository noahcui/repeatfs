import json
import sys
import threading
import time

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeer
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply
from repeatfs.plugins.distributed.timing.timing import VectorClock
from repeatfs.plugins.distributed.utils.logger.basicLogger import Logger

import os

def load_node_id_from_home():
    dir_path = os.path.expanduser("~/.repeatfs")
    file_path = os.path.join(dir_path, "node_id")

    if not os.path.exists(file_path):
        os.makedirs(dir_path, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("0.0\n")

    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read().strip()

    if not raw:
        raw = "0.0"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(raw + "\n")

    return ID.from_string(raw)

class DistributedServer:
    def __init__(self, json_cfg):
        self.json_cfg = json_cfg
        self.local_host = json_cfg.get("host", "0.0.0.0")
        self.local_port = json_cfg["port"]

        self.cfg = Config()
        self.q = msgQueue()
        self.reg = MessagesRegister()

        self.server = BasicTCPPeer(
            id=ID(0, 0),
            local_host=self.local_host,
            local_port=self.local_port,
            msgQ=self.q,
            message_register=self.reg,
            cfg=self.cfg,
        )

        self._register_handlers()
        self.listen_thread = None
        self.running = False

    def _register_handlers(self):
        self.server.register_message(TimestampRequest, self.handle_timestamp_request)

    def handle_timestamp_request(self, netman, msg):
        reply = TimestampReply(
            msg.cid,
            msg.sid,
            msg.msg_id,
            VectorClock.tick(),
        )
        Logger.debug(f"Handled timestamp request from {msg.sid}, reply: {reply}")
        msg.reply(reply)
       

    def start(self):
        if self.running:
            return

        self.listen_thread = threading.Thread(
            target=self.server.listen,
            daemon=True,
            name="distributed-server-listen",
        )
        self.listen_thread.start()
        self.running = True

    def close(self):
        if not self.running:
            return

        self.server.close()

        if self.listen_thread is not None and self.listen_thread.is_alive():
            self.listen_thread.join(timeout=2)

        self.running = False

def load_json_config(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file does not exist: {path}")

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "port" not in cfg:
        raise ValueError("Missing required config field: port")

    cfg.setdefault("host", "0.0.0.0")

    return cfg

def main():
    if len(sys.argv) != 2:
        Logger.info(
            "Usage: python -m repeatfs.plugins.distributed.server <config.json>",
            file=sys.stderr,
        )
        sys.exit(1)

    json_cfg_path = sys.argv[1]
    json_cfg = load_json_config(json_cfg_path)

    server = DistributedServer(json_cfg)
    server.start()
    try:

        while True:

            time.sleep(1)

    except KeyboardInterrupt:

        server.close()

if __name__ == "__main__":
    main()