from ids.nodeID import ID
import json
from logger.basicLogger import Logger


class NodeInfo:
    id = None
    addr = None
    port = None
    is_client = False
    is_reply = False
    conn = None

    def __init__(self, id, addr, port, is_client=False, is_reply=False):
        self.id = id
        self.addr = addr
        self.port = port
        self.is_client = is_client
        self.is_reply = is_reply

    # For registering message, not used in message exchanging.
    def record_conn(self, conn):
        self.conn = conn

    def record_queue(self, queue):
        self.queue = queue

    def __str__(self):
        return "{}(id={}, addr={}, port={}, is_client={}, is_reply={})".format(
            self.__class__.__name__,
            self.id,
            self.addr,
            self.port,
            self.is_client,
            self.is_reply,
        )

    def to_dict(self):
        return {
            "id": str(self.id),
            "addr": self.addr,
            "port": self.port,
            "is_client": self.is_client,
            "is_reply": self.is_reply,
        }

    def marshal(self):
        try:
            return json.dumps(self.to_dict())
        except Exception as e:
            Logger.error(f"Error marshalling message: {e}")
            return None

    @classmethod
    def from_str(cls, str):
        try:
            id = ID.from_string(str.split(",")[0].split("=")[1])
            addr = str.split(",")[1].split("=")[1]
            port = int(str.split(",")[2].split("=")[1])
            is_client = bool(str.split(",")[3].split("=")[1])
            is_reply = bool(str.split(",")[4].split("=")[1])
            return cls(id, addr, port, is_client, is_reply)
        except Exception as e:
            Logger.error(f"Error unmarshalling message: {e}")
            return None

    @classmethod
    def from_dict(cls, obj):
        try:
            return cls(
                ID.from_string(obj["id"]),
                obj["addr"],
                obj["port"],
                obj["is_client"],
                obj["is_reply"],
            )
        except Exception as e:
            Logger.error(f"Error unmarshalling message: {e}")
            return None

    @classmethod
    def unmarshal(cls, data):
        try:
            obj = json.loads(data)
            return cls.from_dict(obj)
        except Exception as e:
            Logger.error(f"Error unmarshalling message: {e}")
            return None
