import json


class message_example:
    conn = None  # For recording which conn it comes from, and/or to reply, not used in message exchanging.
    queue = None  # For recording which queue it comes from, and/or to reply, not used in message exchanging.
    hello = "world"
    index = 1

    def __init__(self, hello, index):
        self.hello = hello
        self.index = index

    def __str__(self):
        return "{}(hello={}, index={})".format(
            self.__class__.__name__, self.hello, self.index
        )

    def to_dict(self):
        return {"hello": self.hello, "index": self.index}

    def marshal(self):
        return json.dumps(self.to_dict())

    # To record the conn.
    def record_conn(self, conn):
        self.conn = conn

    def record_queue(self, queue):
        self.queue = queue

    @classmethod
    def from_str(cls, str):
        hello = str.split(",")[0].split("=")[1]
        index = int(str.split(",")[1].split("=")[1])
        return cls(hello, index)

    @classmethod
    def from_dict(cls, obj):
        return cls(obj["hello"], obj["index"])

    @classmethod
    def unmarshal(cls, data):
        obj = json.loads(data)
        return cls.from_dict(obj)
