from repeatfs.plugins.distributed.utils.errmsgs import IDErr
import argparse


class ID:
    zoon_id = None
    node_id = None
    is_client = None

    def __init__(self, zone_id, node_id, is_client=False):
        if not isinstance(zone_id, int) or not isinstance(node_id, int):
            raise TypeError("zone_id and node_id must be integers")
        if zone_id < 0 or zone_id > 255:
            raise ValueError("zone_id must be in range [0, 255]")
        if node_id < 0 or node_id > 255:
            raise ValueError("node_id must be in range [0, 255]")

        self.zone_id = zone_id
        self.node_id = node_id
        self.is_client = is_client

    def __hash__(self):
        return hash((self.zone_id, self.node_id, self.is_client))

    def __eq__(self, other):
        if not isinstance(other, ID):
            return NotImplemented
        return (self.zone_id, self.node_id, self.is_client) == (
            other.zone_id,
            other.node_id,
            other.is_client,
        )

    @classmethod
    def from_string(cls, id_str):
        if not isinstance(id_str, str):
            raise TypeError("from_string() takes a string as argument")

        parts = id_str.split(".")
        if len(parts) != 2:
            raise ValueError(f"{IDErr.ERROR_WRONG_ID_FORMAT_STR}: {id_str}")

        try:
            zone = int(parts[0])
            node = int(parts[1])
        except ValueError as e:
            raise ValueError(f"{IDErr.ERROR_WRONG_ID_FORMAT_STR}: {e}")

        return cls(zone, node)

    def zone(self):
        return self.zone_id

    def node(self):
        return self.node_id

    def __str__(self):
        return f"{self.zone_id}.{self.node_id}"

    def as_int(self):
        return (self.zone_id << 8) + self.node_id

    def from_int(self, id_int):
        return ID(id_int >> 8, id_int & 0xFF)


def get_id_from_flag():
    parser = argparse.ArgumentParser(description="Process ID flag.")
    parser.add_argument(
        "-id", "--id", default="NotProvided", help="ID in format of Zone.Node."
    )
    args = parser.parse_args()

    if args.id == "NotProvided":
        raise ValueError(IDErr.ERROR_NO_ID_PROVIDED_STR)

    return ID.from_string(args.id)
