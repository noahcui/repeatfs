import json
from repeatfs.plugins.distributed.utils.logger.basicLogger import Logger


class GeneralConfig:
    def __init__(self, file_path=None):
        self.file_path = file_path
        self.load_config(file_path)

    @staticmethod
    def make_default_config():
        return {
            "peers": {
                "1.1": {"addr": "localhost", "port": 50121},
                "1.2": {"addr": "localhost", "port": 50122},
                "1.3": {"addr": "localhost", "port": 50123},
            }
        }

    # def reset_type(self):
    def load_config(self, file_path) -> dict:
        self.config = GeneralConfig.make_default_config()
        try:
            with open(file_path, "r") as f:
                file_config = json.load(f)
            self.update_config(file_config)
            Logger.info(f"Loaded general config from {file_path}")
        except FileNotFoundError:
            Logger.warning(f"Config file not found: {file_path}")
            pass

    @staticmethod
    def get_config_from_string(config_str):
        return json.loads(config_str)

    @staticmethod
    def save_config(file_path, config):
        with open(file_path, "w") as f:
            json.dump(config, f, indent=4)

    def update_config(self, file_config):
        for key, value in file_config["general"].items():
            self.config[key] = value
