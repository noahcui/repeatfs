from repeatfs.plugins.distributed.netman.msg import MSGencoder
from repeatfs.plugins.distributed.utils.logger.basicLogger import Logger
import json


class NetMessageBase:
    def __init__(self):
        self.conn = None
        self.queue = None
        self.replyQueue = None

    def record_conn(self, conn):
        self.conn = conn

    def record_queue(self, queue):
        self.queue = queue

    def record_reply_queue(self, replyQueue):
        self.replyQueue = replyQueue

    def reply(self, reply_msg):
        if self.conn is None:
            Logger.error("Cannot reply: conn is None")
            return False

        encoded = MSGencoder.encode(reply_msg)
        if encoded is None:
            Logger.error("Cannot reply: failed to encode message")
            return False

        try:
            self.conn.send(encoded)
            return True
        except Exception as e:
            Logger.error(f"Cannot reply: {e}")
            return False

    def reply_local(self, obj):
        if self.replyQueue is None:
            Logger.error("Cannot reply locally: replyQueue is None")
            return False

        try:
            self.replyQueue.push(obj)
            return True
        except Exception as e:
            Logger.error(f"Cannot reply locally: {e}")
            return False

    def marshal(self):
        try:
            obj = self.to_dict()
            if obj is None:
                return None
            return json.dumps(obj)
        except Exception as e:
            Logger.error(f"Error marshalling message: {e}")
            return None

    @classmethod
    def unmarshal(cls, data):
        try:
            obj = json.loads(data)
            return cls.from_dict(obj)
        except Exception as e:
            Logger.error(f"Error unmarshalling message: {e}")
            return None