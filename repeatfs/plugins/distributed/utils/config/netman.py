import json


class NetmanConfig:
    def __init__(self, file_path=None):
        self.file_path = file_path
        self.load_config(file_path)

    @staticmethod
    def make_default_config():
        return {
            "type": "basicTCP",
            "conn_send_timeout_ms": 2000,
            "conn_recv_timeout_ms": 2000,
            "nagle": False,
        }

    def load_config(self, file_path) -> dict:
        self.config = NetmanConfig.make_default_config()
        try:
            with open(file_path, "r") as f:
                file_config = json.load(f)
            self.update_config(file_config)
        except FileNotFoundError:
            pass

    @staticmethod
    def get_config_from_string(config_str):
        return json.loads(config_str)

    @staticmethod
    def save_config(file_path, config):
        with open(file_path, "w") as f:
            json.dump(config, f, indent=4)

    def update_config(self, file_config):
        for key, value in file_config["netman"].items():
            self.config[key] = value
