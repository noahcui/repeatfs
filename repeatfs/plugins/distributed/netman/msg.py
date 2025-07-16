import struct
import threading
import queue
from logger.basicLogger import Logger


class MSGencoder:
    @classmethod
    def encode(cls, msg):
        try:
            msg_type = msg.__class__.__name__
            content = msg.marshal()
            encoded_content = content.encode("utf-8")
            encoded_type = msg_type.encode("utf-8")

            length_type = struct.pack("!I", len(encoded_type))
            length_prefix = struct.pack(
                "!I", len(encoded_content) + len(encoded_type) + 4
            )

            return length_prefix + length_type + encoded_type + encoded_content
        except Exception as e:
            Logger.error(f"Error encoding message: {e}")
            return None

    @classmethod
    def decode(cls, encoded_message):
        try:
            length_id = encoded_message[:4]
            type_length = struct.unpack("!I", length_id)[0]
            msg_type = encoded_message[4 : 4 + type_length].decode("utf-8")
            content = encoded_message[4 + type_length :]
            msg_class = MessagesRegister.get_message_class_classmethod(msg_type)
            if msg_class is None:
                Logger.warning(f"Message type not registered: {msg_type}")
                return None
            Logger.debug(f"Decoding {msg_type} with content {content.decode('utf-8')}")
            msg = msg_class.unmarshal(content.decode("utf-8"))
            return msg
        except Exception as e:
            Logger.error(f"Error decoding message: {e}")
            return None

    @classmethod
    def decode_length(cls, encoded_message):
        try:
            length_prefix = encoded_message[:4]
            content_length = struct.unpack("!I", length_prefix)[0]
            return content_length
        except Exception as e:
            Logger.error(f"Error decoding length: {e}")
            return None


class QueueClosed(Exception):
    pass


class msgQueue:
    def __init__(self):
        self.queue = queue.Queue()

    def push(self, msg):
        self.queue.put(msg)

    def pop(self):
        msg = self.queue.get()
        if isinstance(msg, QueueClosed):
            raise QueueClosed()
        return msg

    def size(self):
        return self.queue.qsize()


class MessagesRegister:
    _global_msg_types = {}  # Class-level dictionary to track message types
    _class_lock = threading.Lock()  # Class-level lock

    def __init__(self):
        self._lock = threading.Lock()
        self._msg_classes = {}
        self._msg_handlers = {}
        self.netman = None
        self.queue = None

    def set_netman(self, netman):
        self.netman = netman

    def register_message(self, msg_class, handler):
        with self._lock:  # Instance-level lock
            msg_type = (
                msg_class.__name__
            )  # Use class name as the message type identifier
            if msg_type in self._msg_classes:
                # if already registered, then update the handler.
                self._msg_classes[msg_type] = msg_class
                self._msg_handlers[msg_type] = handler
                Logger.warning(
                    f"Updated existing registration for {msg_class.__name__} with msg_type {msg_type}."
                )
            else:
                self._msg_classes[msg_type] = msg_class
                self._msg_handlers[msg_type] = handler
                Logger.info(
                    f"Registered {msg_class.__name__} with msg_type {msg_type} to handler {handler.__name__}."
                )
            with MessagesRegister._class_lock:  # Use class-level lock for modifying class-level attributes
                MessagesRegister._global_msg_types[msg_type] = (
                    msg_class  # Sync with class-level dictionary
                )

    @classmethod
    def get_message_type_classmethod(cls, msg_class):
        # This class method allows getting message type based on the class rather than an instance
        with cls._class_lock:  # Use class-level lock for accessing class-level attributes
            return (
                msg_class.__name__
                if msg_class.__name__ in cls._global_msg_types
                else None
            )

    @classmethod
    def get_message_class_classmethod(cls, msg_type):
        # This class method allows getting message class based on the type
        with cls._class_lock:
            return cls._global_msg_types.get(msg_type, None)

    def get_message_type(self, message_instance):
        with self._lock:
            msg_type = message_instance.__class__.__name__
            if msg_type in self._msg_classes:
                return msg_type
        return None

    def handle_message(self, msg):
        Logger.debug(f"Handling message: {msg}")
        msg_type = self.get_message_type(msg)
        if msg_type is None:
            Logger.warning(f"Message type not registered: {msg.__class__.__name__}")
            return
        handler = self._msg_handlers[msg_type]
        Logger.debug(f"Handling message {msg_type} with handler {handler.__name__}")
        thread = threading.Thread(target=handler, args=(self.netman, msg), daemon=True)
        thread.start()
        # handler(self.netman, msg)

    def get_message_class(self, msg_type):
        with self._lock:
            return self._msg_classes.get(msg_type, None)

    def start_handling_messages(self, msgQueue: msgQueue):
        Logger.info("Started handling messages.")
        self.queue = msgQueue
        try:
            while True:
                msg = msgQueue.pop()
                if msg is None:
                    continue
                Logger.debug(f"Handling message: {msg}")
                self.handle_message(msg)
        except QueueClosed:
            Logger.info("Stopped handling messages.")

    def stop_handling_messages(self):
        self.queue.push(QueueClosed())
        Logger.info("Stopping handling messages.")
