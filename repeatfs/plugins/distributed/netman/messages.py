import json
from enum import IntEnum

from repeatfs.plugins.distributed.utils.logger.basicLogger import Logger
from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.netman.message_base import NetMessageBase


class MessageType(IntEnum):
    TIMESTAMP_REQUEST = 0x01
    TIMESTAMP_REPLY = 0x02
    JOIN_REQUEST = 0x03
    JOIN_REPLY = 0x04
    DBMERGE_REQUEST = 0x05
    DBMERGE_REPLY = 0x06

    @classmethod
    def to_bytes(cls, msg_type: "MessageType") -> bytes:
        return bytes([msg_type])

    @classmethod
    def from_bytes(cls, data: bytes) -> "MessageType":
        if len(data) != 1:
            raise ValueError(f"message type must be exactly 1 byte, got {len(data)}")
        return cls(data[0])


class TimestampRequest(NetMessageBase):
    msg_type = MessageType.TIMESTAMP_REQUEST

    def __init__(self, cid, sid, msg_id):
        super().__init__()
        self.cid = cid
        self.sid = sid
        self.msg_id = msg_id

    def __str__(self):
        return "{}(cid={}, sid={}, msg_id={})".format(
            self.__class__.__name__, self.cid, self.sid, self.msg_id
        )

    def to_dict(self):
        try:
            return {
                "cid": str(self.cid),
                "sid": str(self.sid),
                "msg_id": self.msg_id,
            }
        except Exception as e:
            Logger.error(f"Error converting message to dict: {e}")
            return None

    @classmethod
    def from_string(cls, s):
        try:
            cid = s.split(",")[0].split("=")[1]
            sid = s.split(",")[1].split("=")[1]
            msg_id = s.split(",")[2].split("=")[1]
            return cls(ID.from_string(cid), ID.from_string(sid), msg_id)
        except Exception as e:
            Logger.error(f"Error unmarshalling message from string: {e}")
            return None

    @classmethod
    def from_dict(cls, obj):
        try:
            return cls(
                cid=ID.from_string(obj["cid"]),
                sid=ID.from_string(obj["sid"]),
                msg_id=obj["msg_id"],
            )
        except Exception as e:
            Logger.error(f"Error unmarshalling message from dict: {e}")
            return None


class TimestampReply(NetMessageBase):
    msg_type = MessageType.TIMESTAMP_REPLY

    def __init__(self, cid, sid, msg_id, time):
        super().__init__()
        self.cid = cid
        self.sid = sid
        self.msg_id = msg_id
        self.time = time

    def __str__(self):
        return "{}(cid={}, sid={}, msg_id={}, time={})".format(
            self.__class__.__name__, self.cid, self.sid, self.msg_id, self.time
        )

    def to_dict(self):
        try:
            return {
                "cid": str(self.cid),
                "sid": str(self.sid),
                "msg_id": self.msg_id,
                "time": self.time,
            }
        except Exception as e:
            Logger.error(f"Error converting message to dict: {e}")
            return None

    @classmethod
    def from_string(cls, s):
        try:
            cid = s.split(",")[0].split("=")[1]
            sid = s.split(",")[1].split("=")[1]
            msg_id = s.split(",")[2].split("=")[1]
            time_val = int(s.split(",")[3].split("=")[1])
            return cls(
                ID.from_string(cid),
                ID.from_string(sid),
                msg_id,
                time_val,
            )
        except Exception as e:
            Logger.error(f"Error unmarshalling message from string: {e}")
            return None

    @classmethod
    def from_dict(cls, obj):
        try:
            return cls(
                cid=ID.from_string(obj["cid"]),
                sid=ID.from_string(obj["sid"]),
                msg_id=obj["msg_id"],
                time=obj["time"],
            )
        except Exception as e:
            Logger.error(f"Error unmarshalling message from dict: {e}")
            return None
        
