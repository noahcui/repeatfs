import time

from repeatfs.plugins.distributed.utils.id.nodeID import ID
from repeatfs.plugins.distributed.utils.config.config import Config
from repeatfs.plugins.distributed.netman.msg import msgQueue, MessagesRegister
from repeatfs.plugins.distributed.netman.netman import BasicTCPPeerClient
from repeatfs.plugins.distributed.netman.messages import TimestampRequest, TimestampReply
from repeatfs.plugins.distributed.netman.waiter import WaiterTable


waiters = WaiterTable()


def handle_timestamp_reply(netman, msg):
    ok = waiters.complete(msg.msg_id, msg)
    if not ok:
        print(f"[client] dropped unmatched reply: {msg}")
    else:
        print(f"[client] received reply: {msg}")


def main():
    cfg = Config()
    q = msgQueue()
    reg = MessagesRegister()

    client = BasicTCPPeerClient(
        id=ID(1, 1, True),
        local_host="127.0.0.1",
        local_port=9001,
        msgQ=q,
        message_register=reg,
        cfg=cfg,
    )

    client.register_message(TimestampReply, handle_timestamp_reply)

    ok = client.connect_to_peer("127.0.0.1", 9000)
    if not ok:
        print("[client] failed to connect")
        return

    print("[client] connected to server")

    time.sleep(1)

    msg_id, waiter = waiters.create()
    
    msg = TimestampRequest(
        cid=ID(1, 1, True),
        sid=ID(0, 0),
        msg_id=msg_id,
    )

    print(f"[client] sending: {msg}")
    client.send(ID(0, 0), msg)

    try:
        reply = waiter.wait(timeout=5)
        if reply is None:
            print(f"[client] timeout waiting for reply, msg_id={msg_id}")
        else:
            print(f"[client] final reply: {reply}")
            print(f"[client] final RTT latency: {waiter.latency_ms:.3f} ms")
    finally:
        waiters.remove(msg_id)

    client.close()
    print("[client] done")


if __name__ == "__main__":
    main()