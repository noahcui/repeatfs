import threading
import time

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister

from repeatfs.plugins.distributed.netman.netman import BasicTCPPeer
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply
import json


def handler(netman, msg):
    # print(f"[server] received: {msg}")

    # 可以顺手回复
    msg.reply(TimestampReply(msg.cid, msg.sid, msg.msg_id, time.time()))


def main():
    cfg = Config()
    q = msgQueue()
    reg = MessagesRegister()

    server = BasicTCPPeer(
        id=ID(0,0),
        local_host="127.0.0.1",
        local_port=9000,
        msgQ=q,
        message_register=reg,
        cfg=cfg,
    )

    # 注册业务消息
    server.register_message(TimestampRequest, handler)

    # 开监听线程
    t = threading.Thread(target=server.listen, daemon=True, name="timehandler-listen-thread")
    t.start()

    print("[server] listening on 127.0.0.1:9000")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.close()


if __name__ == "__main__":
    main()